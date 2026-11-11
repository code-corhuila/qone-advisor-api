"""An Idempotency-Key belongs to the person who sent it (qone-docs#55, data only for its owner): the
same key from another person names another request and never answers their conversation, reply or
plan; the same person reusing a key for a different request is refused with 422
IDEMPOTENCY_KEY_REUSED and nothing is written."""

import pytest

from advisor.application.errors import IdempotencyKeyReused
from tests.fakes import ISW_602_G1, LAURA, SANTIAGO, ScriptedAgent
from tests.http_support import bearer, make_client
from tests.test_advisor_service import LAURA_CALLER, PROPOSE_602, SANTIAGO_CALLER, make

KEY = "shared-key-0001"
CONVERSATION_KEY = "shared-conversation-key"
BASE = "/api/v1/advisor"


def test_two_people_with_the_same_conversation_key_get_their_own_conversation():
    service, store, *_ = make(ScriptedAgent())
    laura = service.start_conversation(LAURA_CALLER, KEY)
    santiago = service.start_conversation(SANTIAGO_CALLER, KEY)
    assert laura.created and santiago.created
    assert santiago.item.id != laura.item.id and santiago.item.user_id == SANTIAGO
    assert service.start_conversation(LAURA_CALLER, KEY).item.id == laura.item.id  # her own replay


def test_two_people_with_the_same_message_key_get_their_own_reply_never_the_others_plan():
    agent = ScriptedAgent(PROPOSE_602, "Te propongo ISW-602 grupo 1.")
    service, store, *_ = make(agent)
    hers = service.start_conversation(LAURA_CALLER, "conversation-laura").item
    his = service.start_conversation(SANTIAGO_CALLER, "conversation-santiago").item
    laura = service.send_message(LAURA_CALLER, hers.id, "Matricúlame ISW-602", KEY)
    santiago = service.send_message(SANTIAGO_CALLER, his.id, "Matricúlame ISW-602", KEY)
    assert laura.created and laura.item.plan.student_id == LAURA
    assert santiago.created and santiago.item.message.conversation_id == his.id
    assert santiago.item.message.id != laura.item.message.id
    assert santiago.item.plan is None  # his tuition is unpaid: never Laura's plan
    assert len(agent.requests) == 2


def test_the_same_key_for_a_different_request_is_refused_and_writes_nothing():
    agent = ScriptedAgent()
    service, store, *_ = make(agent)
    first = service.start_conversation(LAURA_CALLER, "conversation-laura").item
    second = service.start_conversation(LAURA_CALLER, "conversation-laura-2").item
    service.send_message(LAURA_CALLER, first.id, "hola", KEY)
    written = len(store.messages)
    for conversation_id, content in ((first.id, "otra cosa"), (second.id, "hola")):
        with pytest.raises(IdempotencyKeyReused):
            service.send_message(LAURA_CALLER, conversation_id, content, KEY)
    with pytest.raises(IdempotencyKeyReused):
        service.start_conversation(LAURA_CALLER, KEY)  # a message key is not a conversation key
    assert len(store.messages) == written and len(agent.requests) == 1
    replay = service.send_message(LAURA_CALLER, first.id, "hola", KEY)
    assert not replay.created and len(agent.requests) == 1


# --- HTTP, two users
def test_http_santiago_never_gets_lauras_reply_and_a_changed_body_is_422():
    client, *_ = make_client(ScriptedAgent(PROPOSE_602, "Te propongo ISW-602 grupo 1."))
    laura_h, santiago_h = bearer(str(LAURA), "STUDENT", "Laura Gómez"), bearer(str(SANTIAGO), "STUDENT", "Santiago")
    hers = client.post(f"{BASE}/conversations", headers={**laura_h, "Idempotency-Key": CONVERSATION_KEY}).json()["id"]
    his = client.post(f"{BASE}/conversations", headers={**santiago_h, "Idempotency-Key": CONVERSATION_KEY}).json()["id"]
    assert his != hers

    body = {"content": "Matricúlame ISW-602"}
    laura = client.post(f"{BASE}/conversations/{hers}/messages", headers={**laura_h, "Idempotency-Key": KEY}, json=body)
    santiago = client.post(
        f"{BASE}/conversations/{his}/messages", headers={**santiago_h, "Idempotency-Key": KEY}, json=body
    )
    assert laura.status_code == 201 and laura.json()["plan"]["sectionId"] == str(ISW_602_G1)
    assert santiago.status_code == 201 and santiago.json()["plan"] is None
    assert santiago.json()["message"]["id"] != laura.json()["message"]["id"]

    changed = client.post(
        f"{BASE}/conversations/{hers}/messages",
        headers={**laura_h, "Idempotency-Key": KEY, "X-Correlation-Id": "trace-422"},
        json={"content": "otra cosa"},
    )
    assert changed.status_code == 422
    assert changed.json() == {
        "error": "IDEMPOTENCY_KEY_REUSED",
        "message": "the Idempotency-Key was already used for a different request",
        "traceId": "trace-422",
    }
