"""The plan's state rule (INV-ADV-003, D-04) and the conversation and message invariants
(INV-ADV-002, INV-ADV-004), with no framework and no database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from advisor.domain.model.conversation import STORED_CONTENT_MAX, Conversation, Message, MessageRole, UserRole
from advisor.domain.model.errors import InvalidStatusTransition, ValidationError
from advisor.domain.model.events import plan_event
from advisor.domain.model.plan import PLAN_TTL, Plan, PlanStatus, PlanSummary
from advisor.domain.model.slot import Slot

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
LAURA = UUID("11111111-1111-4111-8111-111111111111")
SECTION = UUID("55555555-5555-4555-8555-555555555510")


def uid(n: int) -> UUID:
    return UUID(int=n)


def summary(reason: str = "fits your free Tuesday and Thursday afternoons") -> PlanSummary:
    return PlanSummary(
        "ISW-602", "Teoría de la Computación", 1, "Ana Torres", (Slot.parse("TUE", "16:00", "18:00"),), 30, reason
    )


def proposed() -> Plan:
    return Plan.propose(uid(1), uid(2), LAURA, SECTION, summary(), NOW)


def test_a_proposed_plan_expires_ten_minutes_after_the_proposal():
    plan = proposed()
    assert plan.status is PlanStatus.PROPOSED
    assert plan.expires_at == NOW + PLAN_TTL == NOW + timedelta(minutes=10)
    assert plan.confirmation_id is None and plan.saga_id is None


def test_confirming_records_the_confirmation_that_travels_into_the_saga():
    plan = proposed().confirm(uid(9), NOW + timedelta(minutes=3))
    assert plan.status is PlanStatus.EXECUTING
    assert plan.confirmation_id == uid(9)
    started = plan.started(uid(7), NOW + timedelta(minutes=3))
    assert started.saga_id == uid(7)


def test_an_expired_plan_cannot_be_confirmed_and_expires():
    plan = proposed()
    late = NOW + PLAN_TTL
    with pytest.raises(InvalidStatusTransition, match="expired"):
        plan.confirm(uid(9), late)
    assert plan.is_past_expiry(late)
    assert plan.expire(late).status is PlanStatus.EXPIRED
    assert plan.expire(NOW + timedelta(minutes=9)) is None


def test_only_a_proposed_plan_can_be_confirmed():
    executing = proposed().confirm(uid(9), NOW)
    with pytest.raises(InvalidStatusTransition, match="EXECUTING"):
        executing.confirm(uid(10), NOW)
    dismissed = proposed().dismiss(NOW)
    with pytest.raises(InvalidStatusTransition, match="DISMISSED"):
        dismissed.confirm(uid(10), NOW)


def test_dismissing_a_plan_that_is_not_proposed_changes_nothing():
    plan = proposed()
    assert plan.dismiss(NOW).status is PlanStatus.DISMISSED
    executing = plan.confirm(uid(9), NOW)
    assert executing.dismiss(NOW) is None


def test_the_saga_outcome_settles_an_executing_plan_once():
    executing = proposed().confirm(uid(9), NOW).started(uid(7), NOW)
    done = executing.settle(True, NOW)
    assert done.status is PlanStatus.EXECUTED
    assert done.settle(False, NOW) is None
    assert executing.settle(False, NOW).status is PlanStatus.FAILED
    assert proposed().settle(True, NOW) is None


def test_a_plan_without_a_reason_is_not_proposed():
    with pytest.raises(ValidationError):
        summary("   ")


def test_plan_events_carry_ids_and_state():
    plan = proposed()
    assert plan_event(plan).event_type == "PlanProposed"
    confirmed = plan.confirm(uid(9), NOW)
    event = plan_event(confirmed)
    assert event.event_type == "PlanConfirmed"
    assert event.payload["confirmationId"] == str(uid(9))
    assert plan_event(plan.dismiss(NOW)).event_type == "PlanDismissed"
    assert plan_event(confirmed.settle(True, NOW)) is None


def test_a_conversation_belongs_to_its_user_and_only_people_talk_to_the_advisor():
    conversation = Conversation.start(uid(3), LAURA, "STUDENT", "gpt-4o-mini", NOW)
    assert conversation.user_role is UserRole.STUDENT
    assert conversation.owned_by(LAURA) and not conversation.owned_by(uid(4)) and not conversation.owned_by(None)
    with pytest.raises(ValidationError):
        Conversation.start(uid(3), LAURA, "SERVICE", "gpt-4o-mini", NOW)


def test_a_user_message_is_one_to_two_thousand_characters():
    assert Message.from_user(uid(5), uid(3), "  hola  ", NOW).content == "hola"
    for bad in ("", "   ", None, 7, "x" * 2001):
        with pytest.raises(ValidationError):
            Message.from_user(uid(5), uid(3), bad, NOW)


def test_an_assistant_reply_is_cut_to_the_column_and_never_empty():
    long = Message.from_assistant(uid(6), uid(3), "y" * 9000, NOW)
    assert long.role is MessageRole.ASSISTANT and len(long.content) == STORED_CONTENT_MAX
    assert Message.from_assistant(uid(6), uid(3), "", NOW).content
