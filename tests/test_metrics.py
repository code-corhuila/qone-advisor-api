"""HU-OBS-001 (code-corhuila/qone-docs#56): the RED metrics of every request, the replies by
outcome and the state of the breaker. Core level: the use case reports every new reply through
its port. Adapter: the breaker gauge. HTTP level: `GET /metrics` at the root, route templates."""

from prometheus_client import CollectorRegistry

from advisor.adapter.outbound.llm.breaker import CircuitBreaker
from advisor.adapter.outbound.metrics.prometheus import PrometheusAdvisorMetrics
from advisor.application.port.inbound.caller import Caller, Role
from advisor.application.usecase.advisor import AdvisorService
from tests.fakes import LAURA, DownAgent, FakeSaga, MovableClock, ScriptedAgent, SequenceIds, seeded_advisor
from tests.http_support import bearer, make_client

ME = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
BASE = "/api/v1/advisor"


class RecordingMetrics:
    def __init__(self) -> None:
        self.replies: list[bool] = []

    def reply_recorded(self, degraded: bool) -> None:
        self.replies.append(degraded)


def service(agent, metrics):
    store = seeded_advisor()
    return AdvisorService(store, store, agent, FakeSaga(), MovableClock(), SequenceIds(), "2026-B", metrics=metrics)


def test_every_new_reply_is_reported_with_whether_it_degraded():
    metrics = RecordingMetrics()
    answered = service(ScriptedAgent(), metrics)
    conversation = answered.start_conversation(ME, "conversation-1").item
    answered.send_message(ME, conversation.id, "hola", "message-0001")
    answered.send_message(ME, conversation.id, "hola", "message-0001")  # the replay is not a new reply
    degraded = service(DownAgent(), metrics)
    conversation = degraded.start_conversation(ME, "conversation-1").item
    degraded.send_message(ME, conversation.id, "hola", "message-0001")
    assert metrics.replies == [False, True]


def test_without_a_metrics_port_the_advisor_still_answers():
    store = seeded_advisor()
    advisor = AdvisorService(store, store, None, FakeSaga(), MovableClock(), SequenceIds(), "2026-B")
    conversation = advisor.start_conversation(ME, "conversation-1").item
    assert advisor.send_message(ME, conversation.id, "hola", "message-0001").item.degraded


def test_the_breaker_gauge_is_1_while_the_circuit_is_not_closed():
    registry, now = CollectorRegistry(), [0.0]
    breaker = CircuitBreaker(threshold=1, reset_seconds=60, clock=lambda: now[0])
    PrometheusAdvisorMetrics(registry, breaker)
    assert registry.get_sample_value("advisor_llm_breaker_open") == 0
    breaker.record_failure()
    assert registry.get_sample_value("advisor_llm_breaker_open") == 1
    breaker.record_success()
    assert registry.get_sample_value("advisor_llm_breaker_open") == 0
    without_llm = CollectorRegistry()
    PrometheusAdvisorMetrics(without_llm, None)
    assert without_llm.get_sample_value("advisor_llm_breaker_open") == 0


def sample(client, name, labels=None):
    return client.app.state.metrics_registry.get_sample_value(name, labels or {})


def test_metrics_answers_200_text_plain_with_the_metric_names():
    client, *_ = make_client()
    client.get(f"{BASE}/health")
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "http_server_requests_total",
        "http_server_request_duration_seconds",
        "advisor_llm_replies_total",
        "advisor_llm_breaker_open",
    ):
        assert name in r.text


def test_requests_are_counted_under_their_route_template_and_replies_by_outcome():
    client, *_ = make_client(ScriptedAgent())
    laura = bearer(str(LAURA), "STUDENT", "Laura Gómez")
    conversation = client.post(f"{BASE}/conversations", headers={**laura, "Idempotency-Key": "conversation-1"}).json()
    url = f"{BASE}/conversations/{conversation['id']}/messages"
    for key in ("message-0001", "message-0002"):
        assert client.post(url, headers={**laura, "Idempotency-Key": key}, json={"content": "hola"}).status_code == 201
    client.get(f"{BASE}/conversations/not-a-uuid/messages", headers=laura)
    client.get("/api/v1/nowhere")
    template = f"{BASE}/conversations/{{conversation_id}}/messages"
    labels = {"service": "qone-advisor-api", "method": "POST", "route": template, "status_class": "2xx"}
    assert sample(client, "http_server_requests_total", labels) == 2
    timing = {"service": "qone-advisor-api", "method": "POST", "route": template}
    assert sample(client, "http_server_request_duration_seconds_count", timing) == 2
    bad = {**labels, "method": "GET", "status_class": "4xx"}
    assert sample(client, "http_server_requests_total", bad) == 1
    assert sample(client, "http_server_requests_total", {**bad, "route": "unmatched"}) == 1
    assert sample(client, "advisor_llm_replies_total", {"outcome": "answered"}) == 2
    assert sample(client, "advisor_llm_replies_total", {"outcome": "degraded"}) == 0
    text = client.get("/metrics").text
    assert "not-a-uuid" not in text and 'route="/metrics"' not in text


def test_a_non_standard_method_is_counted_as_other():
    client, *_ = make_client()
    client.request("PURGE", f"{BASE}/health")
    text = client.get("/metrics").text
    assert 'method="OTHER"' in text and 'method="PURGE"' not in text
