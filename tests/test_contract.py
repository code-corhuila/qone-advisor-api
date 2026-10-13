"""Contract level: responses validate against the synced copy of qone-advisor-api.yaml and every
path the contract declares is served."""

import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tests.fakes import ISW_602_G1, LAURA, ScriptedAgent
from tests.http_support import bearer, make_client

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "openapi"
ADVISOR = yaml.safe_load((CONTRACTS / "qone-advisor-api.yaml").read_text(encoding="utf-8"))
SHARED = yaml.safe_load((CONTRACTS / "_shared.yaml").read_text(encoding="utf-8"))
BASE = "/api/v1/advisor"


def inline(node, doc):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            source = SHARED if ref.startswith("./_shared.yaml#") else doc
            target = source
            for part in ref.split("#/")[1].split("/"):
                target = target[part]
            return inline(target, source)
        out = {k: inline(v, doc) for k, v in node.items() if k != "nullable"}
        return {"anyOf": [out, {"type": "null"}]} if node.get("nullable") else out
    if isinstance(node, list):
        return [inline(v, doc) for v in node]
    return node


def assert_valid(body, name: str, doc=ADVISOR):
    schema = inline(doc["components"]["schemas"][name], doc)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    assert not errors, [e.message for e in errors]


def shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_every_path_of_the_contract_is_served():
    client, *_ = make_client()
    served = {shape(p) for p in client.app.openapi()["paths"]}
    for path in ADVISOR["paths"]:
        assert shape("/api/v1" + path) in served, f"{path} is in the contract but not served"


def test_every_representation_matches_its_schema():
    agent = ScriptedAgent(
        [
            ("check_section", {"section_id": str(ISW_602_G1)}),
            ("propose_enrollment", {"section_id": str(ISW_602_G1), "reason": "encaja"}),
        ]
    )
    client, *_ = make_client(agent)
    laura = bearer(str(LAURA), "STUDENT", "Laura Gómez")
    assert_valid(client.get(f"{BASE}/health").json(), "AdvisorHealth")
    conversation = client.post(f"{BASE}/conversations", headers={**laura, "Idempotency-Key": "conversation-1"}).json()
    assert_valid(conversation, "Conversation")
    assert_valid(client.get(f"{BASE}/conversations/me", headers=laura).json(), "ConversationPage")
    url = f"{BASE}/conversations/{conversation['id']}/messages"
    reply = client.post(url, headers={**laura, "Idempotency-Key": "message-1"}, json={"content": "matricúlame"}).json()
    assert_valid(reply, "ChatReply")
    assert_valid(client.get(url, headers=laura).json(), "MessagePage")
    plan_id = reply["plan"]["id"]
    assert_valid(client.get(f"{BASE}/plans/{plan_id}", headers=laura).json(), "Plan")
    assert_valid(client.post(f"{BASE}/plans/{plan_id}/confirm", headers=laura).json(), "PlanConfirmation")
    assert_valid(client.post(f"{BASE}/plans/{plan_id}/dismiss", headers=laura).json(), "Plan")
    error = client.get(f"{BASE}/plans/not-a-uuid", headers=laura).json()
    assert_valid(error, "Error", SHARED)
