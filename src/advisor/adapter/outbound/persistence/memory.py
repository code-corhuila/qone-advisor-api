"""The advisor's store and projections in memory: the same rules as PostgreSQL (the key is claimed
before anything is written, a plan is saved only from the state it was read in, messages are
never edited, a key belongs to its owner and to one request) for the tests and for running the API
without a database."""

from dataclasses import replace
from datetime import datetime
from threading import Lock
from uuid import UUID

from advisor.application.errors import IdempotencyKeyReused
from advisor.application.port.outbound.ports import RequestKey
from advisor.domain.model.advising import SectionView, StudentView, SubjectView
from advisor.domain.model.conversation import Conversation, Message, MessageRole
from advisor.domain.model.events import DomainEvent
from advisor.domain.model.plan import Plan, PlanStatus


class InMemoryAdvisor:
    def __init__(self) -> None:
        self._lock = Lock()
        self.conversations: dict[UUID, Conversation] = {}
        self.messages: list[Message] = []
        self.plans: dict[UUID, Plan] = {}
        self.keys: dict[tuple[UUID, str], tuple[str, UUID, str]] = {}  # (owner, key) -> (type, id, hash)
        self.outbox: list[DomainEvent] = []
        self.subjects: dict[str, SubjectView] = {}
        self.section_views: dict[UUID, SectionView] = {}
        self.students: dict[UUID, StudentView] = {}

    # --- AdvisorStore
    def _resource_of(self, key: RequestKey, resource_type: str) -> UUID | None:
        claimed = self.keys.get((key.owner_id, key.value))
        if claimed is None:
            return None
        if claimed[0] != resource_type or claimed[2] != key.request_hash:
            raise IdempotencyKeyReused()
        return claimed[1]

    def conversation_by_key(self, key: RequestKey) -> Conversation | None:
        found = self._resource_of(key, "conversation")
        return self.conversations.get(found) if found else None

    def create_conversation(self, conversation: Conversation, key: RequestKey) -> tuple[Conversation, bool]:
        with self._lock:
            earlier = self.conversation_by_key(key)
            if earlier is not None:
                return earlier, False
            self.keys[(key.owner_id, key.value)] = ("conversation", conversation.id, key.request_hash)
            self.conversations[conversation.id] = conversation
            return conversation, True

    def conversation(self, conversation_id: UUID) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def conversations_of(self, user_id: UUID, page: int, limit: int) -> tuple[list[Conversation], int]:
        mine = sorted(
            (c for c in self.conversations.values() if c.user_id == user_id), key=lambda c: c.created_at, reverse=True
        )
        return mine[(page - 1) * limit : page * limit], len(mine)

    def messages_of(self, conversation_id: UUID, page: int, limit: int) -> tuple[list[Message], int]:
        mine = [m for m in self.messages if m.conversation_id == conversation_id]
        mine.sort(key=lambda m: m.created_at, reverse=True)
        return mine[(page - 1) * limit : page * limit], len(mine)

    def history(self, conversation_id: UUID, last: int) -> list[Message]:
        mine = sorted((m for m in self.messages if m.conversation_id == conversation_id), key=lambda m: m.created_at)
        return mine[-last:]

    def reply_by_key(self, key: RequestKey) -> tuple[Message, Plan | None] | None:
        found = self._resource_of(key, "reply")
        if found is None:
            return None
        answer = next(m for m in self.messages if m.id == found)
        plan = next(
            (
                p
                for p in self.plans.values()
                if p.conversation_id == answer.conversation_id and p.created_at == answer.created_at
            ),
            None,
        )
        return answer, plan

    def user_messages_since(self, user_id: UUID, since: datetime) -> tuple[int, datetime | None]:
        mine = {c.id for c in self.conversations.values() if c.user_id == user_id}
        sent = [
            m.created_at
            for m in self.messages
            if m.conversation_id in mine and m.role is MessageRole.USER and m.created_at > since
        ]
        return len(sent), min(sent, default=None)

    def record_reply(
        self, question: Message, answer: Message, plan: Plan | None, key: RequestKey, events: list[DomainEvent]
    ) -> tuple[tuple[Message, Plan | None], bool]:
        with self._lock:
            prior = self.reply_by_key(key)
            if prior is not None:
                return prior, False
            self.keys[(key.owner_id, key.value)] = ("reply", answer.id, key.request_hash)
            self.messages += [question, answer]
            if plan is not None:
                self.plans[plan.id] = plan
            self.outbox += events
            return (answer, plan), True

    def plan(self, plan_id: UUID) -> Plan | None:
        return self.plans.get(plan_id)

    def save_plan(self, plan: Plan, expected: PlanStatus, event: DomainEvent | None) -> bool:
        with self._lock:
            current = self.plans.get(plan.id)
            if current is None or current.status is not expected:
                return False
            self.plans[plan.id] = plan
            if event is not None:
                self.outbox.append(event)
            return True

    def settle_by_saga(self, saga_id: UUID, succeeded: bool, at) -> bool:
        """What the consumer does with an enrollment outcome: only an EXECUTING plan moves."""
        with self._lock:
            plan = next((p for p in self.plans.values() if p.saga_id == saga_id), None)
            settled = plan.settle(succeeded, at) if plan else None
            if settled is None:
                return False
            self.plans[settled.id] = settled
            return True

    # --- Projections
    def pensum(self) -> list[SubjectView]:
        return list(self.subjects.values())

    def subject(self, code: str) -> SubjectView | None:
        return self.subjects.get(code)

    def sections(self, term: str, query: str | None = None) -> list[SectionView]:
        q = (query or "").casefold()
        found = [
            s
            for s in self.section_views.values()
            if s.term == term and (not q or q in s.subject_code.casefold() or q in s.subject_name.casefold())
        ]
        return sorted(found, key=lambda s: (s.subject_code, s.group_number))

    def section(self, section_id: UUID) -> SectionView | None:
        return self.section_views.get(section_id)

    def student(self, student_id: UUID) -> StudentView:
        return self.students.get(student_id) or StudentView(student_id)

    def put_student(self, view: StudentView) -> None:
        self.students[view.student_id] = view

    def put_seats(self, section_id: UUID, seats: int) -> None:
        self.section_views[section_id] = replace(self.section_views[section_id], seats_available=seats)
