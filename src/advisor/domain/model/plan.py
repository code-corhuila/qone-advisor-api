"""The enrollment plan: what the agent proposes and only the student can confirm (INV-ADV-001,
INV-ADV-003). PROPOSED -> EXECUTING -> EXECUTED | FAILED; PROPOSED -> DISMISSED | EXPIRED.
A plan expires 10 minutes after it was proposed; confirming it records the confirmation id that
travels into the saga (INV-ENR-005); dismissing a plan that is no longer PROPOSED changes
nothing (D-04). The summary is frozen at proposal time: it is what the student confirmed."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from advisor.domain.model.errors import FieldError, InvalidStatusTransition, ValidationError
from advisor.domain.model.slot import Slot

PLAN_TTL = timedelta(minutes=10)
REASON_MAX = 500


class PlanStatus(StrEnum):
    PROPOSED = "PROPOSED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    DISMISSED = "DISMISSED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class PlanSummary:
    subject_code: str
    subject_name: str
    group_number: int
    professor_name: str
    slots: tuple[Slot, ...]
    seats_available: int
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValidationError([FieldError("reason", "the agent must say why it proposes this section")])
        object.__setattr__(self, "reason", self.reason.strip()[:REASON_MAX])


@dataclass(frozen=True)
class Plan:
    id: UUID
    conversation_id: UUID
    student_id: UUID
    section_id: UUID
    summary: PlanSummary
    status: PlanStatus
    confirmation_id: UUID | None
    saga_id: UUID | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def propose(
        plan_id: UUID, conversation_id: UUID, student_id: UUID, section_id: UUID, summary: PlanSummary, now: datetime
    ) -> "Plan":
        return Plan(
            plan_id,
            conversation_id,
            student_id,
            section_id,
            summary,
            PlanStatus.PROPOSED,
            None,
            None,
            now + PLAN_TTL,
            now,
            now,
        )

    def is_past_expiry(self, now: datetime) -> bool:
        return self.status is PlanStatus.PROPOSED and now >= self.expires_at

    def confirm(self, confirmation_id: UUID, now: datetime) -> "Plan":
        if self.status is not PlanStatus.PROPOSED:
            raise InvalidStatusTransition(f"a {self.status.value} plan cannot be confirmed")
        if now >= self.expires_at:
            raise InvalidStatusTransition("the plan expired: ask the advisor for a new one (INV-ADV-003)")
        return replace(self, status=PlanStatus.EXECUTING, confirmation_id=confirmation_id, updated_at=now)

    def started(self, saga_id: UUID, now: datetime) -> "Plan":
        if self.status is not PlanStatus.EXECUTING:
            raise InvalidStatusTransition(f"a {self.status.value} plan does not start a saga")
        return replace(self, saga_id=saga_id, updated_at=now)

    def settle(self, succeeded: bool, now: datetime) -> "Plan | None":
        """The saga's outcome; None when it was already applied (outcomes arrive more than once)."""
        if self.status is not PlanStatus.EXECUTING:
            return None
        return replace(self, status=PlanStatus.EXECUTED if succeeded else PlanStatus.FAILED, updated_at=now)

    def dismiss(self, now: datetime) -> "Plan | None":
        """PROPOSED -> DISMISSED; None for any other state: the plan is returned unchanged (D-04)."""
        if self.status is not PlanStatus.PROPOSED:
            return None
        return replace(self, status=PlanStatus.DISMISSED, updated_at=now)

    def expire(self, now: datetime) -> "Plan | None":
        if not self.is_past_expiry(now):
            return None
        return replace(self, status=PlanStatus.EXPIRED, updated_at=now)
