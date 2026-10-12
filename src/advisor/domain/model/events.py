"""Events the advisor publishes through its outbox (02-domain/domain-events.md): PlanProposed,
PlanConfirmed, PlanDismissed. The payload carries ids and the state, never the conversation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from advisor.domain.model.plan import Plan, PlanStatus

_TYPES = {
    PlanStatus.PROPOSED: "PlanProposed",
    PlanStatus.EXECUTING: "PlanConfirmed",
    PlanStatus.DISMISSED: "PlanDismissed",
}


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime


def plan_event(plan: Plan) -> DomainEvent | None:
    """The event of the state the plan just reached; None for states that publish nothing."""
    event_type = _TYPES.get(plan.status)
    if event_type is None:
        return None
    payload = {
        "planId": str(plan.id),
        "studentId": str(plan.student_id),
        "sectionId": str(plan.section_id),
        "subjectCode": plan.summary.subject_code,
        "status": plan.status.value,
        "confirmationId": str(plan.confirmation_id) if plan.confirmation_id else None,
        "expiresAt": plan.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return DomainEvent(event_type, "plan", plan.id, payload, plan.updated_at)
