"""Security P0 (code-corhuila/qone-docs#53, threat D on TB6): a per-user quota of messages in a
sliding hour stops the request at its origin, before anything reaches OpenAI, and every reply
has a known maximum cost (`max_tokens`). Domain, use case, HTTP and composition root levels."""

from datetime import UTC, datetime, timedelta

import pytest
from apps.api.factory import Settings, build_app

from advisor.application.errors import TooManyRequests
from advisor.application.port.inbound.caller import Caller, Role
from advisor.application.usecase.advisor import AdvisorService
from advisor.domain.model.quota import MessageQuota
from tests.fakes import LAURA, SANTIAGO, FakeSaga, MovableClock, ScriptedAgent, SequenceIds, seeded_advisor
from tests.http_support import PUBLIC_PEM, bearer, make_client
from tests.test_contract import SHARED, assert_valid

LAURA_CALLER = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
SANTIAGO_CALLER = Caller(SANTIAGO, Role.STUDENT, "Santiago Rojas", str(SANTIAGO))
T0 = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
BASE = "/api/v1/advisor"


# --- domain: the rule of the sliding hour
def test_under_the_limit_there_is_nothing_to_wait():
    assert MessageQuota(3).retry_after(2, T0, T0 + timedelta(minutes=5)) is None
    assert MessageQuota(3).retry_after(0, None, T0) is None


def test_at_the_limit_the_wait_is_until_the_oldest_message_leaves_the_hour():
    quota = MessageQuota(3)
    assert quota.window_start(T0) == T0 - timedelta(hours=1)
    assert quota.retry_after(3, T0, T0) == 3600
    assert quota.retry_after(3, T0, T0 + timedelta(minutes=50, seconds=30.5)) == 570  # rounded up
    assert quota.retry_after(3, T0, T0 + timedelta(hours=1)) == 1  # never 0


def test_the_limit_is_positive():
    with pytest.raises(ValueError):
        MessageQuota(0)


# --- use case: the agent is not called past the quota
def make(per_hour=2):
    store, clock, agent = seeded_advisor(), MovableClock(), ScriptedAgent()
    service = AdvisorService(
        store, store, agent, FakeSaga(), clock, SequenceIds(), "2026-B", messages_per_hour=per_hour
    )
    return service, store, clock, agent


def talk(service, caller, key, content="hola"):
    conversation = service.start_conversation(caller, f"conversation-of-{caller.user_id}").item
    return service.send_message(caller, conversation.id, content, key)


def test_the_message_past_the_quota_is_refused_before_the_agent():
    service, store, clock, agent = make()
    talk(service, LAURA_CALLER, "message-0001")
    clock.advance(10)
    talk(service, LAURA_CALLER, "message-0002")
    with pytest.raises(TooManyRequests) as refused:
        talk(service, LAURA_CALLER, "message-0003")
    assert refused.value.retry_after == 50 * 60 and refused.value.limit == 2
    assert len(agent.requests) == 2 and len(store.messages) == 4, "nothing sent, nothing written"


def test_the_quota_counts_every_conversation_of_the_user_and_only_theirs():
    service, store, clock, agent = make()
    first = service.start_conversation(LAURA_CALLER, "conversation-a").item
    second = service.start_conversation(LAURA_CALLER, "conversation-b").item
    service.send_message(LAURA_CALLER, first.id, "hola", "message-a-01")
    service.send_message(LAURA_CALLER, second.id, "hola", "message-b-01")
    with pytest.raises(TooManyRequests):
        service.send_message(LAURA_CALLER, second.id, "hola", "message-b-02")
    assert talk(service, SANTIAGO_CALLER, "message-santiago-1").created, "another student is not affected"


def test_the_hour_slides_and_a_repeated_key_is_still_answered():
    service, _, clock, agent = make()
    talk(service, LAURA_CALLER, "message-0001")
    talk(service, LAURA_CALLER, "message-0002")
    replay = talk(service, LAURA_CALLER, "message-0002")
    assert not replay.created and len(agent.requests) == 2, "the replay sends nothing and is not refused"
    clock.advance(60)
    assert talk(service, LAURA_CALLER, "message-0003").created and len(agent.requests) == 3


# --- HTTP: the 31st message within the hour (acceptance criterion 1)
def test_the_31st_message_of_the_hour_is_429_with_retry_after_and_the_agent_is_not_called():
    agent = ScriptedAgent()
    client, _, clock, _ = make_client(agent)
    laura = bearer(str(LAURA), "STUDENT", "Laura Gómez")
    conversation = client.post(f"{BASE}/conversations", headers={**laura, "Idempotency-Key": "conversation-1"}).json()
    url = f"{BASE}/conversations/{conversation['id']}/messages"
    for n in range(30):
        sent = client.post(url, headers={**laura, "Idempotency-Key": f"message-{n:04d}"}, json={"content": "hola"})
        assert sent.status_code == 201
        clock.advance(1)
    refused = client.post(
        url,
        headers={**laura, "Idempotency-Key": "message-0030", "X-Correlation-Id": "quota-31"},
        json={"content": "hola"},
    )
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == str(30 * 60)
    body = refused.json()
    assert body == {
        "error": "TOO_MANY_REQUESTS",
        "message": "message quota reached: at most 30 messages per hour",
        "traceId": "quota-31",
    }
    assert_valid(body, "Error", SHARED)
    assert len(agent.requests) == 30, "stopped at its origin, before the boundary"


# --- composition root: the limits come from the environment (acceptance criterion 2)
ENV = {"JWT_PUBLIC_KEY": PUBLIC_PEM, "OPENAI_API_KEY": "sk-test", "SERVICE_TOKEN": "t"}


def agent_of(app):
    return app.state.use_cases._agent


def test_chat_openai_is_built_with_max_tokens_800_by_default():
    chat = agent_of(build_app(Settings(ENV)))._chat_factory(5.0)
    assert chat.max_tokens == 800


def test_the_limits_come_from_the_environment():
    settings = Settings({**ENV, "ADVISOR_MAX_TOKENS": "500", "ADVISOR_MESSAGES_PER_HOUR": "12"})
    app = build_app(settings)
    assert agent_of(app)._chat_factory(5.0).max_tokens == 500
    assert app.state.use_cases._quota.per_hour == 12
    assert Settings(ENV).messages_per_hour == 30


@pytest.mark.parametrize("name", ["ADVISOR_MAX_TOKENS", "ADVISOR_MESSAGES_PER_HOUR"])
@pytest.mark.parametrize("value", ["0", "-3", "many"])
def test_a_limit_that_is_not_a_positive_integer_stops_the_start(name, value):
    with pytest.raises(SystemExit):
        Settings({**ENV, name: value})
