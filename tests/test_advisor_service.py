"""The use cases over the in-memory store: conversations, grounded and degraded replies, the
tools closed over the caller, and the plan from proposal to the saga's outcome."""

from datetime import timedelta
from uuid import UUID

import pytest

from advisor.application.errors import Forbidden, NotFound, ServiceUnavailable
from advisor.application.port.inbound.caller import Caller, Role
from advisor.application.usecase.advisor import DEGRADED_REPLY, AdvisorService
from advisor.domain.model.errors import InvalidStatusTransition, ValidationError
from advisor.domain.model.plan import PlanStatus
from tests.fakes import (
    CARLOS,
    ISW_602_G1,
    ISW_604_G1,
    ISW_604_G2,
    LAURA,
    SANTIAGO,
    DownAgent,
    FakeSaga,
    MovableClock,
    ScriptedAgent,
    SequenceIds,
    seeded_advisor,
)

LAURA_CALLER = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
SANTIAGO_CALLER = Caller(SANTIAGO, Role.STUDENT, "Santiago Rojas", str(SANTIAGO))
CARLOS_CALLER = Caller(CARLOS, Role.PROFESSOR, "Carlos Ramírez", str(CARLOS))
PROPOSE_602 = [
    ("check_section", {"section_id": str(ISW_602_G1)}),
    ("propose_enrollment", {"section_id": str(ISW_602_G1), "reason": "encaja en tus tardes libres"}),
]


def make(agent=None, saga=None):
    store, clock, saga = seeded_advisor(), MovableClock(), saga or FakeSaga()
    return AdvisorService(store, store, agent, saga, clock, SequenceIds(), "2026-B"), store, clock, saga


def chat(service, caller=LAURA_CALLER, text="¿Qué me falta para graduarme?", key="message-key-1"):
    conversation = service.start_conversation(caller, f"conv-{caller.user_id}").item
    return conversation, service.send_message(caller, conversation.id, text, key)


def test_a_conversation_is_created_once_per_key_and_only_for_people():
    service, *_ = make(ScriptedAgent())
    first = service.start_conversation(LAURA_CALLER, "conversation-1")
    again = service.start_conversation(LAURA_CALLER, "conversation-1")
    assert first.created and not again.created and again.item.id == first.item.id
    assert first.item.model == "scripted-model"
    with pytest.raises(Forbidden):
        service.start_conversation(Caller(None, Role.SERVICE, "", "qone-workflow"), "conversation-2")
    with pytest.raises(ValidationError):
        service.start_conversation(LAURA_CALLER, "short")


def test_a_grounded_reply_reads_the_callers_own_audit():
    agent = ScriptedAgent([("degree_audit", {})], "Te faltan ISW-602 e ISW-604.")
    service, store, *_ = make(agent)
    conversation, reply = chat(service)
    assert reply.created and not reply.item.degraded and reply.item.plan is None
    audit = agent.results[0]
    assert {s["code"]: s["state"] for s in audit["subjects"]}["ISW-601"] == "IN_PROGRESS"
    assert agent.requests[0].history[-1].content == "¿Qué me falta para graduarme?"
    page = service.messages(LAURA_CALLER, conversation.id, 1, 20)
    assert [m.role.value for m in page.items] == ["ASSISTANT", "USER"] and page.total == 2


def test_the_tools_take_no_user_id_so_the_model_cannot_act_for_another_student():
    agent = ScriptedAgent([("tuition_status", {"student_id": str(SANTIAGO)})])
    service, *_ = make(agent)
    chat(service)
    for tool in agent.requests[0].tools:
        assert not any("student" in p or "user" in p for p in tool.parameters.get("properties", {}))
    assert agent.results[0]["status"] == "PAID"  # Laura's, whatever the arguments say


def test_without_the_agent_the_reply_degrades_and_is_still_created():
    for agent in (None, DownAgent()):
        service, *_ = make(agent)
        conversation, reply = chat(service)
        assert reply.created and reply.item.degraded and reply.item.message.content == DEGRADED_REPLY
        again = service.send_message(LAURA_CALLER, conversation.id, "otra vez", "message-key-1")
        assert not again.created and again.item.degraded and again.item.message.id == reply.item.message.id


def test_another_users_conversation_does_not_exist():
    service, *_ = make(ScriptedAgent())
    conversation, _ = chat(service)
    with pytest.raises(NotFound):
        service.messages(SANTIAGO_CALLER, conversation.id, 1, 20)
    with pytest.raises(NotFound):
        service.send_message(SANTIAGO_CALLER, conversation.id, "hola", "message-key-2")


def test_a_proposal_that_passes_the_gates_becomes_a_proposed_plan():
    service, store, clock, _ = make(ScriptedAgent(PROPOSE_602, "Te propongo ISW-602 grupo 1."))
    _, reply = chat(service, text="Matricúlame Teoría de la Computación")
    plan = reply.item.plan
    assert plan.status is PlanStatus.PROPOSED and plan.section_id == ISW_602_G1 and plan.student_id == LAURA
    assert plan.summary.subject_code == "ISW-602" and plan.summary.seats_available == 30
    assert plan.expires_at - plan.created_at == timedelta(minutes=10)
    assert [e.event_type for e in store.outbox] == ["PlanProposed"]
    replay = service.send_message(LAURA_CALLER, plan.conversation_id, "x", "message-key-1")
    assert replay.item.plan.id == plan.id


def test_a_proposal_the_saga_would_reject_creates_no_plan():
    for caller, section, blocker in (
        (LAURA_CALLER, ISW_604_G2, "SCHEDULE_CONFLICT"),
        (SANTIAGO_CALLER, ISW_604_G1, "TUITION_UNPAID"),
    ):
        agent = ScriptedAgent([("propose_enrollment", {"section_id": str(section), "reason": "x"})])
        service, store, *_ = make(agent)
        _, reply = chat(service, caller=caller)
        assert reply.item.plan is None and blocker in agent.results[0] and store.outbox == []


def test_a_professor_only_lists_sections_without_student_data():
    agent = ScriptedAgent([("list_sections", {"subject": "ISW-604"}), ("degree_audit", {})])
    service, *_ = make(agent)
    chat(service, caller=CARLOS_CALLER)
    assert [t.name for t in agent.requests[0].tools] == ["list_sections"]
    assert len(agent.results[0]) == 3 and "blockers" not in agent.results[0][0]
    assert agent.results[1] == "unknown tool degree_audit"


def proposed(saga=None):
    service, store, clock, saga = make(ScriptedAgent(PROPOSE_602), saga)
    _, reply = chat(service)
    return service, store, clock, saga, reply.item.plan


def test_confirming_starts_the_saga_for_the_student_with_the_confirmation():
    service, store, _, saga, plan = proposed()
    done = service.confirm(LAURA_CALLER, plan.id, "trace-1")
    student, section, confirmation, key, trace = saga.calls[0]
    assert (student, section, key, trace) == (LAURA, ISW_602_G1, f"advisor-plan-{plan.id}", "trace-1")
    assert confirmation == done.plan.confirmation_id is not None
    assert done.plan.status is PlanStatus.EXECUTED and done.plan.saga_id == done.saga_id
    assert [e.event_type for e in store.outbox] == ["PlanProposed", "PlanConfirmed"]
    with pytest.raises(InvalidStatusTransition):
        service.confirm(LAURA_CALLER, plan.id)


def test_a_rejected_saga_fails_the_plan_and_a_running_one_waits_for_the_event():
    service, *_, plan = proposed(FakeSaga("COMPENSATED", "NO_SEATS"))
    assert service.confirm(LAURA_CALLER, plan.id).plan.status is PlanStatus.FAILED
    service, store, _, _, plan = proposed(FakeSaga("RUNNING"))
    waiting = service.confirm(LAURA_CALLER, plan.id).plan
    assert waiting.status is PlanStatus.EXECUTING
    assert store.settle_by_saga(waiting.saga_id, True, waiting.updated_at)
    assert not store.settle_by_saga(waiting.saga_id, False, waiting.updated_at)
    assert service.plan(LAURA_CALLER, plan.id).status is PlanStatus.EXECUTED


def test_an_expired_plan_is_not_confirmed_and_reads_as_expired():
    service, _, clock, saga, plan = proposed()
    clock.advance(10.5)
    with pytest.raises(InvalidStatusTransition, match="expired"):
        service.confirm(LAURA_CALLER, plan.id)
    assert saga.calls == []
    assert service.plan(LAURA_CALLER, plan.id).status is PlanStatus.EXPIRED


def test_when_the_saga_does_not_answer_confirming_again_retries_with_the_same_confirmation():
    saga = FakeSaga()
    saga.down = True
    service, store, _, _, plan = proposed(saga)
    with pytest.raises(ServiceUnavailable):
        service.confirm(LAURA_CALLER, plan.id)
    assert store.plan(plan.id).status is PlanStatus.EXECUTING and store.plan(plan.id).saga_id is None
    saga.down = False
    done = service.confirm(LAURA_CALLER, plan.id)
    assert done.plan.status is PlanStatus.EXECUTED
    assert saga.calls[0][2] == saga.calls[1][2] and saga.calls[0][3] == saga.calls[1][3]


def test_dismissing_is_idempotent_and_leaves_other_states_unchanged():
    service, *_, plan = proposed()
    assert service.dismiss(LAURA_CALLER, plan.id).status is PlanStatus.DISMISSED
    assert service.dismiss(LAURA_CALLER, plan.id).status is PlanStatus.DISMISSED
    service, *_, plan = proposed()
    service.confirm(LAURA_CALLER, plan.id)
    assert service.dismiss(LAURA_CALLER, plan.id).status is PlanStatus.EXECUTED


def test_another_students_plan_does_not_exist():
    service, *_, plan = proposed()
    for action in (service.plan, service.confirm, service.dismiss):
        with pytest.raises(NotFound):
            action(SANTIAGO_CALLER, plan.id)
    with pytest.raises(NotFound):
        service.plan(LAURA_CALLER, UUID(int=1))


def test_pages_are_validated():
    service, *_ = make(ScriptedAgent())
    with pytest.raises(ValidationError):
        service.my_conversations(LAURA_CALLER, 0, 101)
    chat(service)
    assert service.my_conversations(LAURA_CALLER, 1, 20).total == 1
