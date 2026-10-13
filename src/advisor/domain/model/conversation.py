"""A conversation and its messages (AD-02..AD-05). A conversation belongs to the user of the token
that started it, and that token's role chooses its tool set (INV-ADV-002). Messages are
append-only (INV-ADV-004): nothing in this module edits or deletes one."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from advisor.domain.model.errors import FieldError, ValidationError

USER_CONTENT_MAX = 2000  # the contract's limit for what a user sends
STORED_CONTENT_MAX = 8000  # the column's limit; a longer reply is cut, never rejected


class UserRole(StrEnum):
    STUDENT = "STUDENT"
    PROFESSOR = "PROFESSOR"
    ADMIN = "ADMIN"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


@dataclass(frozen=True)
class Conversation:
    id: UUID
    user_id: UUID
    user_role: UserRole
    model: str
    created_at: datetime

    @staticmethod
    def start(conversation_id: UUID, user_id: UUID, role: str, model: str, now: datetime) -> "Conversation":
        try:
            user_role = UserRole(role)
        except ValueError:
            raise ValidationError(
                [FieldError("role", "only a STUDENT, PROFESSOR or ADMIN talks to the advisor")]
            ) from None
        return Conversation(conversation_id, user_id, user_role, (model or "none")[:80], now)

    def owned_by(self, user_id: UUID | None) -> bool:
        return user_id is not None and self.user_id == user_id


@dataclass(frozen=True)
class Message:
    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    created_at: datetime

    @staticmethod
    def from_user(message_id: UUID, conversation_id: UUID, content: object, now: datetime) -> "Message":
        if not isinstance(content, str) or not content.strip():
            raise ValidationError([FieldError("content", "must be a non-empty string")])
        if len(content) > USER_CONTENT_MAX:
            raise ValidationError([FieldError("content", f"must be at most {USER_CONTENT_MAX} characters")])
        return Message(message_id, conversation_id, MessageRole.USER, content.strip(), now)

    @staticmethod
    def from_assistant(message_id: UUID, conversation_id: UUID, content: str, now: datetime) -> "Message":
        text = (content or "").strip() or "..."
        return Message(message_id, conversation_id, MessageRole.ASSISTANT, text[:STORED_CONTENT_MAX], now)
