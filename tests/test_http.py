"""HTTP level: the token rules, the status codes of AD-01..AD-08, the error envelope with the
correlation id, and the checks the piece page names (degraded reply, expired plan)."""

from tests.fakes import ISW_602_G1, LAURA, SANTIAGO, ScriptedAgent
from tests.http_support import OTHER_PRIVATE, bearer, make_client, mint

BASE = "/api/v1/advisor"
LAURA_H = bearer(str(LAURA), "STUDENT", "Laura Gómez")
PROPOSE = [
    ("check_section", {"section_id": str(ISW_602_G1)}),
    ("propose_enrollment", {"section_id": str(ISW_602_G1), "reason": "encaja en tus tardes"}),
]


def keyed(headers: dict, key: str) -> dict:
    return {**headers, "Idempotency-Key": key}


def conversation(client, headers=LAURA_H) -> str:
    response = client.post(f"{BASE}/conversations", headers=keyed(headers, "conversation-1"))
    assert response.status_code == 201
    return response.json()["id"]


def test_health_is_public_and_says_whether_the_llm_is_configured():
    client, *_ = make_client()
    body = client.get(f"{BASE}/health").json()
    assert body == {"status": "ok", "service": "qone-advisor-api", "version": "2.0.0", "llm": "missing", "model": None}
    client, *_ = make_client(ScriptedAgent())
    assert client.get(f"{BASE}/health").json()["llm"] == "configured"


def test_tokens_are_rs256_signed_by_identity_and_current():
    client, *_ = make_client()
    for headers in (
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": f"Bearer {mint(str(LAURA), 'STUDENT', alg='HS256')}"},
        {"Authorization": f"Bearer {mint(str(LAURA), 'STUDENT', key=OTHER_PRIVATE)}"},
        {"Authorization": f"Bearer {mint(str(LAURA), 'STUDENT', minutes=-5)}"},
    ):
        response = client.get(f"{BASE}/conversations/me", headers={**headers, "X-Correlation-Id": "trace-401"})
        assert response.status_code == 401
        assert response.json()["error"] == "UNAUTHORIZED" and response.json()["traceId"] == "trace-401"


def test_a_conversation_is_created_once_per_key():
    client, *_ = make_client()
    first = client.post(f"{BASE}/conversations", headers=keyed(LAURA_H, "conversation-1"))
    again = client.post(f"{BASE}/conversations", headers=keyed(LAURA_H, "conversation-1"))
    assert first.status_code == 201 and again.status_code == 200
    assert first.headers["Location"].endswith(f"/conversations/{first.json()['id']}/messages")
    assert again.json()["id"] == first.json()["id"]
    assert client.post(f"{BASE}/conversations", headers=LAURA_H).status_code == 400
    page = client.get(f"{BASE}/conversations/me", headers=LAURA_H).json()
    assert page["meta"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}
    assert client.get(f"{BASE}/conversations/me?limit=101", headers=LAURA_H).status_code == 400


def test_without_the_llm_a_reply_is_degraded_and_still_201():
    client, *_ = make_client()
    cid = conversation(client)
    response = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "hola"}
    )
    assert response.status_code == 201 and response.json()["degraded"] is True and response.json()["plan"] is None
    again = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "x"}
    )
    assert again.status_code == 200 and again.json()["message"]["id"] == response.json()["message"]["id"]
    listed = client.get(f"{BASE}/conversations/{cid}/messages", headers=LAURA_H).json()
    assert [m["role"] for m in listed["data"]] == ["ASSISTANT", "USER"]


def test_a_message_is_validated_at_the_border():
    client, *_ = make_client()
    cid = conversation(client)
    url = f"{BASE}/conversations/{cid}/messages"
    for body in ({}, {"content": 5}, {"content": ""}, {"content": "x" * 2001}):
        response = client.post(url, headers=keyed(LAURA_H, "message-1"), json=body)
        assert response.status_code == 400 and response.json()["details"][0]["field"] == "content"
    assert client.post(url, headers=LAURA_H, json={"content": "hola"}).status_code == 400
    assert (
        client.post(
            f"{BASE}/conversations/not-a-uuid/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "a"}
        ).status_code
        == 400
    )


def test_another_users_conversation_and_plan_are_404():
    client, *_ = make_client(ScriptedAgent(PROPOSE))
    cid = conversation(client)
    reply = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "a"}
    )
    plan_id = reply.json()["plan"]["id"]
    santiago = bearer(str(SANTIAGO), "STUDENT")
    assert client.get(f"{BASE}/conversations/{cid}/messages", headers=santiago).status_code == 404
    assert client.get(f"{BASE}/plans/{plan_id}", headers=santiago).status_code == 404
    assert client.post(f"{BASE}/plans/{plan_id}/confirm", headers=santiago).json()["error"] == "NOT_FOUND"


def test_a_plan_is_proposed_then_confirmed_with_202_and_the_saga_id():
    client, _, _, saga = make_client(ScriptedAgent(PROPOSE))
    cid = conversation(client)
    reply = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "a"}
    )
    plan = reply.json()["plan"]
    assert plan["status"] == "PROPOSED" and plan["confirmationId"] is None
    response = client.post(f"{BASE}/plans/{plan['id']}/confirm", headers={**LAURA_H, "X-Correlation-Id": "trace-9"})
    assert response.status_code == 202
    body = response.json()
    assert body["plan"]["status"] == "EXECUTED" and body["sagaId"] == body["plan"]["sagaId"]
    assert saga.calls[0][4] == "trace-9"
    again = client.post(f"{BASE}/plans/{plan['id']}/confirm", headers=LAURA_H)
    assert again.status_code == 422 and again.json()["error"] == "INVALID_STATUS_TRANSITION"


def test_an_expired_plan_is_422_and_dismissing_is_idempotent():
    client, _, clock, _ = make_client(ScriptedAgent(PROPOSE))
    cid = conversation(client)
    plan_id = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "a"}
    ).json()["plan"]["id"]
    clock.advance(11)
    assert client.post(f"{BASE}/plans/{plan_id}/confirm", headers=LAURA_H).status_code == 422
    assert client.get(f"{BASE}/plans/{plan_id}", headers=LAURA_H).json()["status"] == "EXPIRED"
    dismissed = client.post(f"{BASE}/plans/{plan_id}/dismiss", headers=LAURA_H)
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "EXPIRED"


def test_a_saga_that_does_not_answer_is_503():
    client, _, _, saga = make_client(ScriptedAgent(PROPOSE))
    saga.down = True
    cid = conversation(client)
    plan_id = client.post(
        f"{BASE}/conversations/{cid}/messages", headers=keyed(LAURA_H, "message-1"), json={"content": "a"}
    ).json()["plan"]["id"]
    response = client.post(f"{BASE}/plans/{plan_id}/confirm", headers=LAURA_H)
    assert response.status_code == 503 and response.json()["error"] == "SERVICE_UNAVAILABLE"


def test_a_service_token_cannot_talk_to_the_advisor_and_unknown_routes_are_404():
    client, *_ = make_client()
    service = bearer("qone-workflow", "SERVICE")
    assert client.post(f"{BASE}/conversations", headers=keyed(service, "conversation-1")).status_code == 403
    response = client.get(f"{BASE}/nowhere", headers=LAURA_H)
    assert response.status_code == 404 and response.json()["error"] == "NOT_FOUND"
