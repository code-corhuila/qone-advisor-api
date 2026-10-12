"""The advisor's store and projections in memory: the same rules as PostgreSQL (the key is claimed
before anything is written, a plan is saved only from the state it was read in, messages are
never edited) for the tests and for running the API without a database."""

from dataclasses import replace
from threading import Lock
from uuid import UUID

from advisor.domain.model.advising import SectionView, StudentView, SubjectView
from advisor.domain.model.conversation import Conversation, Message
from advisor.domain.model.events import DomainEvent
from advisor.domain.model.plan import Plan, PlanStatus


class InMemoryAdvisor:
    def __init__(self) -> None:
        self._lock = Lock()
        self.conversations: dict[UUID, Conversation] = {}
        self.messages: list[Message] = []
        self.plans: dict[UUID, Plan] = {}
        self.keys: dict[str, tuple[str, UUID]] = {}
        self.outbox: list[DomainEvent] = []
        self.subjects: dict[str, SubjectView] = {}
        self.section_views: dict[UUID, SectionView] = {}
        self.students: dict[UUID, StudentView] = {}

    # --- AdvisorStore
    def conversation_by_key(self, idempotency_key: str) -> Conversation | None:
        claimed = self.keys.get(idempotency_key)
        return self.conversations.get(claimed[1]) if claimed and claimed[0] == "conversation" else None

    def create_conversation(self, conversation: Conversation, idempotency_key: str) -> tuple[Conversation, bool]:
        with self._lock:
            if idempotency_key in self.keys:
                return self.conversations[self.keys[idempotency_key][1]], False
            self.keys[idempotency_key] = ("conversation", conversation.id)
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

    def reply_by_key(self, idempotency_key: str) -> tuple[Message, Plan | None] | None:
        claimed = self.keys.get(idempotency_key)
        if not claimed or claimed[0] != "reply":
            return None
        answer = next(m for m in self.messages if m.id == claimed[1])
        plan = next(
            (
                p
                for p in self.plans.values()
                if p.conversation_id == answer.conversation_id and p.created_at == answer.created_at
            ),
            None,
        )
        return answer, plan

    def record_reply(
        self, question: Message, answer: Message, plan: Plan | None, idempotency_key: str, events: list[DomainEvent]
    ) -> tuple[tuple[Message, Plan | None], bool]:
        with self._lock:
            if idempotency_key in self.keys:
                prior = self.reply_by_key(idempotency_key)
                assert prior is not None
                return prior, False
            self.keys[idempotency_key] = ("reply", answer.id)
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
