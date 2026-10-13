"""The saga client over a mocked transport (service token, AGENT origin, the plan's key, retries
only on 429/5xx/network) and the mapping of catalog, enrollment and billing events to changes."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from advisor.adapter.outbound.saga.http import HttpSagaStarter
from advisor.application.port.outbound.ports import SagaUnavailable
from advisor.application.usecase.projections import IncomingEvent, ProjectEvents, changes_of

LAURA, SECTION, CONFIRMATION = uuid4(), uuid4(), uuid4()
SAGA = "77777777-7777-4777-8777-777777777701"


def starter(responses: list) -> tuple[HttpSagaStarter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpSagaStarter("http://qone-workflow:8000/", "service-token", client, sleep=lambda _: None), seen


def saga(status: str, reason=None, code: int = 201) -> httpx.Response:
    return httpx.Response(code, json={"id": SAGA, "status": status, "reason": reason})


def test_the_saga_is_started_with_the_service_token_for_the_student():
    client, seen = starter([saga("COMPLETED")])
    result = client.start(LAURA, SECTION, CONFIRMATION, "advisor-plan-1", "trace-1")
    assert (str(result.saga_id), result.status) == (SAGA, "COMPLETED")
    request = seen[0]
    assert str(request.url) == "http://qone-workflow:8000/api/v1/sagas/enroll-student"
    assert request.headers["Authorization"] == "Bearer service-token"
    assert request.headers["Idempotency-Key"] == "advisor-plan-1" and request.headers["X-Correlation-Id"] == "trace-1"
    body = httpx.Response(200, content=request.content).json()
    assert body == {
        "studentId": str(LAURA),
        "sectionId": str(SECTION),
        "origin": "AGENT",
        "confirmationId": str(CONFIRMATION),
    }


def test_a_repeated_start_returns_the_same_saga_and_its_reason():
    client, _ = starter([saga("COMPENSATED", "NO_SEATS", code=200)])
    result = client.start(LAURA, SECTION, CONFIRMATION, "advisor-plan-1", "")
    assert (result.status, result.reason) == ("COMPENSATED", "NO_SEATS")


def test_network_errors_429_and_5xx_are_retried_then_unavailable():
    client, seen = starter([httpx.ConnectError("down"), httpx.Response(503), saga("COMPLETED")])
    assert client.start(LAURA, SECTION, CONFIRMATION, "advisor-plan-1", "").status == "COMPLETED"
    assert len(seen) == 3
    client, seen = starter([httpx.Response(429), httpx.Response(502), httpx.Response(504)])
    with pytest.raises(SagaUnavailable):
        client.start(LAURA, SECTION, CONFIRMATION, "advisor-plan-1", "")
    assert len(seen) == 3


def test_any_other_answer_is_final():
    client, seen = starter([httpx.Response(403, json={"error": "FORBIDDEN"})])
    with pytest.raises(SagaUnavailable, match="403"):
        client.start(LAURA, SECTION, CONFIRMATION, "advisor-plan-1", "")
    assert len(seen) == 1


def event(kind: str, **payload) -> IncomingEvent:
    return IncomingEvent(uuid4(), kind, payload, datetime(2026, 10, 13, tzinfo=UTC))


def test_events_become_changes_of_the_projections_and_the_plans():
    assert changes_of(event("SeatCommitted", sectionId="s", seatsAvailable=26, reservationId="r"), "2026-B") == [
        ("seats", {"sectionId": "s", "seatsAvailable": 26})
    ]
    assert changes_of(event("TuitionPaid", studentId="u", term="2026-B"), "2026-B") == [
        ("tuition", {"studentId": "u", "status": "PAID"})
    ]
    confirmed = event(
        "EnrollmentConfirmed", studentId="u", sectionId="s", subjectCode="ISW-602", term="2026-B", sagaId="g"
    )
    assert changes_of(confirmed, "2026-B") == [
        ("enrolled", {"studentId": "u", "sectionId": "s", "subjectCode": "ISW-602"}),
        ("outcome", {"sagaId": "g", "status": "EXECUTED"}),
    ]
    rejected = event("EnrollmentRejected", studentId="u", sectionId="s", term="2026-B", sagaId="g")
    assert changes_of(rejected, "2026-B") == [("outcome", {"sagaId": "g", "status": "FAILED"})]
    cancelled = event("EnrollmentCancelled", studentId="u", sectionId="s", term="2026-B", sagaId="g")
    assert changes_of(cancelled, "2026-B") == [("cancelled", {"studentId": "u", "sectionId": "s"})]


def test_other_terms_and_unknown_events_are_ignored():
    assert changes_of(event("InvoiceIssued", studentId="u", term="2027-A"), "2026-B") == []
    assert changes_of(event("CertificateIssued", studentId="u", term="2026-B"), "2026-B") == []

    class Store:
        def __init__(self):
            self.seen = set()

        def apply(self, event_id, changes, occurred_at):
            fresh = event_id not in self.seen
            self.seen.add(event_id)
            return fresh

    project = ProjectEvents(Store(), "2026-B")
    paid = event("TuitionPaid", studentId="u", term="2026-B")
    assert [project.handle(paid), project.handle(paid)] == ["applied", "duplicate"]
    assert project.handle(event("PlanProposed", planId="p")) == "ignored"


def test_a_new_section_is_projected_whole():
    keys = (
        "sectionId",
        "subjectCode",
        "subjectName",
        "term",
        "groupNumber",
        "professorName",
        "slots",
        "seatsAvailable",
    )
    created = event("SectionCreated", **{k: k for k in keys}, extra="ignored")
    assert changes_of(created, "2026-B") == [("section", {k: k for k in keys})]
    assert UUID(SAGA)
