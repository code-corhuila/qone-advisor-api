"""The composition root: what the service does with and without its optional settings."""

import pytest
from apps.api.factory import Settings, build_app
from fastapi.testclient import TestClient

from tests.fakes import LAURA
from tests.http_support import PUBLIC_PEM, bearer

ENV = {"JWT_PUBLIC_KEY": PUBLIC_PEM.replace("\n", "\n")}


def test_the_public_key_is_required():
    with pytest.raises(SystemExit):
        Settings({})


def test_without_key_and_token_it_starts_degraded_in_memory():
    client = TestClient(build_app(Settings(ENV)))
    assert client.get("/api/v1/advisor/health").json()["llm"] == "missing"
    laura = {**bearer(str(LAURA), "STUDENT"), "Idempotency-Key": "conversation-1"}
    conversation = client.post("/api/v1/advisor/conversations", headers=laura).json()
    assert conversation["model"] == "none"
    reply = client.post(
        f"/api/v1/advisor/conversations/{conversation['id']}/messages",
        headers={**laura, "Idempotency-Key": "message-1"},
        json={"content": "hola"},
    )
    assert reply.status_code == 201 and reply.json()["degraded"] is True


def test_with_a_key_the_health_names_the_model():
    settings = Settings({**ENV, "OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "gpt-4o-mini", "SERVICE_TOKEN": "t"})
    body = TestClient(build_app(settings)).get("/api/v1/advisor/health").json()
    assert (body["llm"], body["model"]) == ("configured", "gpt-4o-mini")
