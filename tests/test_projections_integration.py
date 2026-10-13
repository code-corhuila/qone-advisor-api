"""Integration level: events applied to the real projections and plans of qone-advisor-db, once each,
in one transaction. Throwaway database only (TEST_DATABASE_URL); it restores the seed rows it touches."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from advisor.application.usecase.projections import IncomingEvent, ProjectEvents

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
SANTIAGO = "11111111-1111-4111-8111-111111111112"
ISW_602_G1 = "55555555-5555-4555-8555-555555555510"
AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def setup():
    from advisor.adapter.outbound.persistence.postgres import PostgresProjections, connect
    from advisor.adapter.outbound.persistence.projections import PostgresProjectionStore

    pool = connect(URL, max_size=2)
    with pool.connection() as conn:
        before = conn.execute("SELECT * FROM advisor.student_view WHERE student_id = %s", (SANTIAGO,)).fetchone()
        seats = conn.execute(
            "SELECT seats_available, updated_at FROM advisor.catalog_section_view WHERE section_id = %s", (ISW_602_G1,)
        ).fetchone()
        conn.execute("TRUNCATE advisor.inbox")
    yield ProjectEvents(PostgresProjectionStore(pool), "2026-B"), PostgresProjections(pool), pool
    with pool.connection() as conn:
        from psycopg.types.json import Jsonb

        conn.execute(
            "UPDATE advisor.student_view SET confirmed_sections = %s, tuition_status = %s, updated_at = %s WHERE "
            "student_id = %s",
            (Jsonb(before["confirmed_sections"]), before["tuition_status"], before["updated_at"], SANTIAGO),
        )
        conn.execute(
            "UPDATE advisor.catalog_section_view SET seats_available = %s, updated_at = %s WHERE section_id = %s",
            (seats["seats_available"], seats["updated_at"], ISW_602_G1),
        )
        conn.execute("TRUNCATE advisor.inbox, advisor.plan, advisor.message, advisor.conversation")
    pool.close()


def event(kind: str, at: datetime = AT, **payload) -> IncomingEvent:
    return IncomingEvent(uuid4(), kind, payload, at)


def test_tuition_and_enrollments_move_the_student_view_once(setup):
    project, projections, _ = setup
    paid = event("TuitionPaid", studentId=SANTIAGO, term="2026-B")
    assert project.handle(paid) == "applied" and project.handle(paid) == "duplicate"
    assert project.handle(event("InvoiceIssued", studentId=SANTIAGO, term="2026-B")) == "applied"
    assert projections.student(UUID(SANTIAGO)).tuition_status == "PAID"  # PAID never goes back
    enrolled = dict(studentId=SANTIAGO, sectionId=ISW_602_G1, subjectCode="ISW-602", term="2026-B", sagaId=str(uuid4()))
    project.handle(event("EnrollmentConfirmed", **enrolled))
    view = projections.student(UUID(SANTIAGO))
    assert [c.subject_code for c in view.confirmed] == ["ISW-602"] and len(view.confirmed[0].slots) == 2
    project.handle(event("EnrollmentCancelled", **enrolled))
    assert projections.student(UUID(SANTIAGO)).confirmed == ()


def test_a_late_seat_event_does_not_overwrite_a_newer_one(setup):
    project, projections, _ = setup
    later, earlier = AT + timedelta(days=30), AT + timedelta(days=29)
    project.handle(event("SeatReserved", at=later, sectionId=ISW_602_G1, seatsAvailable=12))
    project.handle(event("SeatReleased", at=earlier, sectionId=ISW_602_G1, seatsAvailable=13))
    assert projections.section(UUID(ISW_602_G1)).seats_available == 12


def test_the_outcome_settles_only_an_executing_plan_of_that_saga(setup):
    project, _, pool = setup
    saga_id, conversation, plan = uuid4(), uuid4(), uuid4()
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO advisor.conversation (id, user_id, user_role, model) VALUES (%s, %s, 'STUDENT', 'm')",
            (conversation, SANTIAGO),
        )
        conn.execute(
            "INSERT INTO advisor.plan (id, conversation_id, student_id, section_id, summary, status, confirmation_id, "
            "saga_id, "
            "expires_at, created_at) VALUES (%s, %s, %s, %s, '{}', 'EXECUTING', %s, %s, now() + interval '10 "
            "minutes', now())",
            (plan, conversation, SANTIAGO, ISW_602_G1, uuid4(), saga_id),
        )
    project.handle(
        event("EnrollmentRejected", studentId=SANTIAGO, sectionId=ISW_602_G1, term="2026-B", sagaId=str(saga_id))
    )
    project.handle(
        event(
            "EnrollmentConfirmed",
            studentId=SANTIAGO,
            sectionId=ISW_602_G1,
            subjectCode="ISW-602",
            term="2026-B",
            sagaId=str(saga_id),
        )
    )
    with pool.connection() as conn:
        assert conn.execute("SELECT status FROM advisor.plan WHERE id = %s", (plan,)).fetchone()["status"] == "FAILED"
