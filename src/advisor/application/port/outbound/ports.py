"""What the advisor needs (Annex C, outbound ports): its store, its projections, the language
model behind the agent and the client of the enrollment saga."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from advisor.domain.model.advising import SectionView, StudentView, SubjectView
from advisor.domain.model.conversation import Conversation, Message, UserRole
from advisor.domain.model.events import DomainEvent
from advisor.domain.model.plan import Plan, PlanStatus


class AdvisorStore(Protocol):
    def conversation_by_key(self, idempotency_key: str) -> Conversation | None: ...

    def create_conversation(self, conversation: Conversation, idempotency_key: str) -> tuple[Conversation, bool]:
        """Claims the key first: a repeated key returns the conversation created the first time."""

    def conversation(self, conversation_id: UUID) -> Conversation | None: ...

    def conversations_of(self, user_id: UUID, page: int, limit: int) -> tuple[list[Conversation], int]: ...

    def messages_of(self, conversation_id: UUID, page: int, limit: int) -> tuple[list[Message], int]:
        """Newest first (AD-04)."""

    def history(self, conversation_id: UUID, last: int) -> list[Message]:
        """The last `last` messages, oldest first: what the agent reads before answering."""

    def reply_by_key(self, idempotency_key: str) -> tuple[Message, Plan | None] | None: ...

    def record_reply(
        self, question: Message, answer: Message, plan: Plan | None, idempotency_key: str, events: list[DomainEvent]
    ) -> tuple[tuple[Message, Plan | None], bool]:
        """One transaction: the key, both messages, the plan and its events. A repeated key keeps the
        first reply and writes nothing."""

    def plan(self, plan_id: UUID) -> Plan | None: ...

    def save_plan(self, plan: Plan, expected: PlanStatus, event: DomainEvent | None) -> bool:
        """Writes the plan only if it is still in `expected` (a concurrent change wins); the event in the
        same transaction. False when the plan moved meanwhile."""


class Projections(Protocol):
    def pensum(self) -> list[SubjectView]: ...

    def subject(self, code: str) -> SubjectView | None: ...

    def sections(self, term: str, query: str | None = None) -> list[SectionView]:
        """Sections of the term; with a query, those whose subject code or name contains it."""

    def section(self, section_id: UUID) -> SectionView | None: ...

    def student(self, student_id: UUID) -> StudentView: ...


@dataclass(frozen=True)
class Tool:
    """A function the agent may call: a name, what it does, the JSON schema of its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class AgentRequest:
    role: UserRole
    term: str
    history: list[Message]
    tools: list[Tool]


class AgentUnavailable(Exception):
    """No key, the circuit is open or the provider failed: the reply degrades (D-02)."""


class Agent(Protocol):
    @property
    def model(self) -> str | None: ...

    def reply(self, request: AgentRequest) -> str:
        """The assistant's answer after any tool calls; raises AgentUnavailable."""


@dataclass(frozen=True)
class SagaResult:
    saga_id: UUID
    status: str  # RUNNING, COMPLETED, COMPENSATED, FAILED (qone-workflow WF-02)
    reason: str | None = None


class SagaUnavailable(Exception):
    """qone-workflow did not answer; the plan stays EXECUTING and confirming again retries."""


class SagaStarter(Protocol):
    def start(
        self, student_id: UUID, section_id: UUID, confirmation_id: UUID, idempotency_key: str, correlation_id: str
    ) -> SagaResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...
