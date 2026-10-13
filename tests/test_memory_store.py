"""The in-memory store keeps the rules the PostgreSQL store keeps: a key is claimed once, a reply is
found again by its key with its plan, a plan is saved only from the state it was read in."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from advisor.domain.model.conversation import Conversation, Message
from advisor.domain.model.events import plan_event
from advisor.domain.model.plan import Plan, PlanStatus, PlanSummary
from tests.fakes import ISW_602_G1, LAURA, seeded_advisor, slots

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def conversation(n: int = 1) -> Conversation:
    return Conversation.start(UUID(int=n), LAURA, "STUDENT", "m", NOW + timedelta(minutes=n))


def plan_at(conversation_id: UUID, at: datetime) -> Plan:
    summary = PlanSummary("ISW-602", "Teoría", 1, "Ana Torres", slots(("TUE", "16:00", "18:00")), 30, "porque sí")
    return Plan.propose(UUID(int=50), conversation_id, LAURA, ISW_602_G1, summary, at)


def test_a_conversation_key_is_claimed_once():
    store = seeded_advisor()
    first, created = store.create_conversation(conversation(1), "conversation-key")
    again, created_again = store.create_conversation(conversation(2), "conversation-key")
    assert created and not created_again and again.id == first.id
    assert store.conversation_by_key("conversation-key") == first
    store.create_conversation(conversation(3), "another-key")
    items, total = store.conversations_of(LAURA, 1, 1)
    assert total == 2 and items[0].id == UUID(int=3)


def test_a_reply_is_recorded_once_with_its_plan_and_events():
    store = seeded_advisor()
    c, _ = store.create_conversation(conversation(), "conversation-key")
    question = Message.from_user(UUID(int=10), c.id, "hola", NOW)
    answer = Message.from_assistant(UUID(int=11), c.id, "te propongo", NOW + timedelta(seconds=1))
    plan = plan_at(c.id, answer.created_at)
    (message, stored), created = store.record_reply(question, answer, plan, "reply-key-1", [plan_event(plan)])
    assert created and message == answer and stored == plan
    other = Message.from_assistant(UUID(int=12), c.id, "otra", NOW)
    (message, stored), created = store.record_reply(question, other, None, "reply-key-1", [])
    assert not created and message == answer and stored == plan
    assert store.reply_by_key("reply-key-1") == (answer, plan)
    assert [e.event_type for e in store.outbox] == ["PlanProposed"]
    assert [m.id for m in store.history(c.id, 10)] == [question.id, answer.id]
    assert [m.id for m in store.messages_of(c.id, 1, 1)[0]] == [answer.id]


def test_a_plan_is_saved_only_from_the_state_it_was_read_in():
    store = seeded_advisor()
    c, _ = store.create_conversation(conversation(), "conversation-key")
    plan = plan_at(c.id, NOW)
    store.record_reply(
        Message.from_user(UUID(int=10), c.id, "x", NOW),
        Message.from_assistant(UUID(int=11), c.id, "y", NOW),
        plan,
        "reply-key-1",
        [],
    )
    dismissed = plan.dismiss(NOW)
    assert store.save_plan(dismissed, PlanStatus.PROPOSED, plan_event(dismissed))
    confirmed = plan.confirm(UUID(int=9), NOW)
    assert not store.save_plan(confirmed, PlanStatus.PROPOSED, plan_event(confirmed))
    assert store.plan(plan.id).status is PlanStatus.DISMISSED


def test_the_projections_answer_by_term_and_by_code_or_name():
    store = seeded_advisor()
    assert [s.group_number for s in store.sections("2026-B", "isw-604")] == [1, 2, 3]
    assert [s.subject_code for s in store.sections("2026-B", "teoría")] == ["ISW-602"]
    assert store.sections("2027-A") == []
    assert store.student(UUID(int=404)).tuition_status is None
    assert store.subject("ISW-604").prerequisites == ("RED-501",)
