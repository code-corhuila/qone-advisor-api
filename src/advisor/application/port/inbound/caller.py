"""Who calls a use case: the subject and the role of a validated token. A SERVICE token (the
workflow) carries the service name as subject and no user id."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    STUDENT = "STUDENT"
    PROFESSOR = "PROFESSOR"
    ADMIN = "ADMIN"
    SERVICE = "SERVICE"


@dataclass(frozen=True)
class Caller:
    user_id: UUID | None
    role: Role
    name: str = ""
    subject: str = ""
