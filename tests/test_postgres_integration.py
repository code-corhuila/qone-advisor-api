"""Integration level (Annex C): the real engine with the schema and seeds of qone-advisor-db, through
TEST_DATABASE_URL. Skipped, not failed, without it. It empties the conversation tables, so point it
only at a throwaway database, never at the platform's."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from advisor.application.port.inbound.caller import Caller, Role
from advisor.application.usecase.advisor import AdvisorService
from advisor.domain.model.plan import PlanStatus
from tests.fakes import ISW_602_G1, ISW_604_G2, LAURA, SANTIAGO, FakeSaga, MovableClock, ScriptedAgent, SequenceIds

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
ME = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))


class RandomIds(SequenceIds):
    def new_id(self) -> UUID:
        return uuid4()


@pytest.fixture(scope="module")
def pool():
    from advisor.adapter.outbound.persistence.postgres import connect

    pool = connect(URL, max_size=2)
    yield pool
    pool.close()


@pytest.fixture
def ports(pool):
    from advisor.adapter.outbound.persistence.postgres import PostgresAdvisorStore, PostgresProjections

    with pool.connection() as conn:
        conn.execute(
            "TRUNCATE advisor.plan, advisor.message, advisor.conversation, advisor.idempotency_key, advisor.outbox, "
            "advisor.llm_cache"
        )
    return PostgresAdvisorStore(pool), PostgresProjections(pool)


def service(ports, agent, saga=None, clock=None):
    store, projections = ports
    return AdvisorService(store, projections, agent, saga or FakeSaga(), clock or MovableClock(), RandomIds(), "2026-B")


def propose(section=ISW_602_G1):
    return ScriptedAgent([("propose_enrollment", {"section_id": str(section), "reason": "encaja en tu horario"})])


def test_the_projections_come_from_the_seed(ports):
    _, projections = ports
    assert len(projections.pensum()) == 14 and projections.subject("ISW-604").prerequisites == ("RED-501",)
    assert [s.group_number for s in projections.sections("2026-B", "sistemas")] == [1, 2, 3]
    laura = projections.student(LAURA)
    assert laura.tuition_status == "PAID" and {c.subject_code for c in laura.confirmed} == {
        "ISW-601",
        "ISW-603",
        "ISW-605",
        "MAT-501",
    }
    assert projections.student(SANTIAGO).tuition_status == "ISSUED"


def test_a_reply_with_a_plan_is_written_once_and_found_again_by_its_key(ports, pool):
    advisor = service(ports, propose())
    conversation = advisor.start_conversation(ME, "it-conversation").item
    assert not advisor.start_conversation(ME, "it-conversation").created
    first = advisor.send_message(ME, conversation.id, "matricúlame ISW-602", "it-message-1")
    again = advisor.send_message(ME, conversation.id, "otra vez", "it-message-1")
    assert first.created and not again.created
    assert again.item.message.id == first.item.message.id and again.item.plan.id == first.item.plan.id
    assert first.item.plan.summary.slots == again.item.plan.summary.slots
    page = advisor.messages(ME, conversation.id, 1, 20)
    assert [m.role.value for m in page.items] == ["ASSISTANT", "USER"] and page.total == 2
    with pool.connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM advisor.outbox WHERE event_type = 'PlanProposed'").fetchone()["n"]
            == 1
        )


def test_a_blocked_section_is_not_proposed_against_the_real_projections(ports):
    advisor = service(ports, propose(ISW_604_G2))
    conversation = advisor.start_conversation(ME, "it-conversation").item
    assert advisor.send_message(ME, conversation.id, "ISW-604 grupo 2", "it-message-1").item.plan is None


def test_the_plan_moves_only_from_the_state_it_was_read_in(ports):
    saga = FakeSaga("RUNNING")
    advisor = service(ports, propose(), saga)
    conversation = advisor.start_conversation(ME, "it-conversation").item
    plan = advisor.send_message(ME, conversation.id, "x", "it-message-1").item.plan
    executing = advisor.confirm(ME, plan.id).plan
    assert executing.status is PlanStatus.EXECUTING and executing.saga_id is not None and executing.confirmation_id
    store, _ = ports
    assert not store.save_plan(executing, PlanStatus.PROPOSED, None)
    assert advisor.dismiss(ME, plan.id).status is PlanStatus.EXECUTING


def test_an_expired_plan_reads_as_expired(ports):
    clock = MovableClock()
    advisor = service(ports, propose(), clock=clock)
    conversation = advisor.start_conversation(ME, "it-conversation").item
    plan = advisor.send_message(ME, conversation.id, "x", "it-message-1").item.plan
    clock.advance(11)
    assert advisor.plan(ME, plan.id).status is PlanStatus.EXPIRED


def test_the_llm_cache_serves_what_it_stored(pool):
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration

    from advisor.adapter.outbound.llm.cache import PostgresLlmCache

    cache = PostgresLlmCache(pool)
    assert cache.lookup("prompt", "gpt-4o-mini") is None
    cache.update("prompt", "gpt-4o-mini", [ChatGeneration(message=AIMessage(content="hola"))])
    assert cache.lookup("prompt", "gpt-4o-mini")[0].message.content == "hola"
    with pool.connection() as conn:
        conn.execute("UPDATE advisor.llm_cache SET created_at = %s", (datetime.now(UTC) - timedelta(days=8),))
    assert cache.lookup("prompt", "gpt-4o-mini") is None
