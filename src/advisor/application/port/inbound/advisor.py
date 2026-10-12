"""What the advisor offers (Annex C, inbound ports): AD-01..AD-08 as results and the use-case
interface the HTTP adapter depends on."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from advisor.application.port.inbound.caller import Caller
from advisor.domain.model.conversation import Conversation, Message
from advisor.domain.model.plan import Plan

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class Created[T]:
    item: T
    created: bool


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    limit: int
    total: int

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.limit) if self.total else 0


@dataclass(frozen=True)
class Health:
    llm_configured: bool
    model: str | None


@dataclass(frozen=True)
class ChatReply:
    message: Message
    plan: Plan | None
    degraded: bool


@dataclass(frozen=True)
class Confirmation:
    plan: Plan
    saga_id: UUID


class AdvisorUseCases(Protocol):
    def health(self) -> Health: ...

    def start_conversation(self, caller: Caller, idempotency_key: str) -> Created[Conversation]: ...

    def my_conversations(self, caller: Caller, page: int, limit: int) -> Page[Conversation]: ...

    def messages(self, caller: Caller, conversation_id: UUID, page: int, limit: int) -> Page[Message]: ...

    def send_message(
        self, caller: Caller, conversation_id: UUID, content: object, idempotency_key: str
    ) -> Created[ChatReply]: ...

    def plan(self, caller: Caller, plan_id: UUID) -> Plan: ...

    def confirm(self, caller: Caller, plan_id: UUID, correlation_id: str = "") -> Confirmation: ...

    def dismiss(self, caller: Caller, plan_id: UUID) -> Plan: ...
