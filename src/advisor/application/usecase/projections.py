"""Keeps the advisor's projections and the outcome of its plans from the events of catalog,
enrollment and billing (D-11), consumed from RabbitMQ. Idempotent: the event id goes to the inbox
in the same transaction as the changes, and a repeated delivery is skipped. A late event never
overwrites a newer state; a PAID tuition never goes back to ISSUED; an outcome only moves an
EXECUTING plan. Events of another term and unknown types are acknowledged and ignored."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

log = logging.getLogger("advisor.projections")

Change = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class IncomingEvent:
    event_id: UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime


class ProjectionStore(Protocol):
    def apply(self, event_id: UUID, changes: list[Change], occurred_at: datetime) -> bool:
        """The inbox row and every change in one transaction; False when the event was already consumed."""


SEAT_EVENTS = {"SeatReserved", "SeatReleased", "SeatCommitted"}
SECTION_KEYS = (
    "sectionId",
    "subjectCode",
    "subjectName",
    "term",
    "groupNumber",
    "professorName",
    "slots",
    "seatsAvailable",
)


def changes_of(event: IncomingEvent, term: str) -> list[Change]:
    p, kind = event.payload, event.event_type
    if kind == "SubjectCreated":
        return [("subject", {k: p[k] for k in ("subjectId", "code", "name", "credits", "semester", "prerequisites")})]
    if kind == "SectionCreated":
        return [("section", {k: p[k] for k in SECTION_KEYS})]
    if kind in SEAT_EVENTS:
        return [("seats", {"sectionId": p["sectionId"], "seatsAvailable": p["seatsAvailable"]})]
    if p.get("term") not in (None, term):
        return []
    if kind in ("InvoiceIssued", "TuitionPaid"):
        return [("tuition", {"studentId": p["studentId"], "status": "PAID" if kind == "TuitionPaid" else "ISSUED"})]
    if kind == "EnrollmentConfirmed":
        enrolled = {"studentId": p["studentId"], "sectionId": p["sectionId"], "subjectCode": p["subjectCode"]}
        return [("enrolled", enrolled), ("outcome", {"sagaId": p["sagaId"], "status": "EXECUTED"})]
    if kind == "EnrollmentRejected":
        return [("outcome", {"sagaId": p["sagaId"], "status": "FAILED"})]
    if kind == "EnrollmentCancelled":
        return [("cancelled", {"studentId": p["studentId"], "sectionId": p["sectionId"]})]
    return []


class ProjectEvents:
    def __init__(self, store: ProjectionStore, current_term: str) -> None:
        self._store = store
        self._term = current_term

    def handle(self, event: IncomingEvent, correlation_id: str = "") -> str:
        changes = changes_of(event, self._term)
        if not changes:
            return "ignored"
        applied = self._store.apply(event.event_id, changes, event.occurred_at)
        log.info("event", extra={"traceId": correlation_id, "event": event.event_type, "applied": applied})
        return "applied" if applied else "duplicate"
