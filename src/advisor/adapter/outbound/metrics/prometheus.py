"""The advisor's metrics port on Prometheus (HU-OBS-001, ADR-010): replies by outcome and whether
the breaker around the provider is open, registered in the registry that `GET /metrics` exposes."""

from prometheus_client import CollectorRegistry, Counter, Gauge

from advisor.adapter.outbound.llm.breaker import CircuitBreaker

# The buckets of every latency histogram of the platform: the SLOs (p95) are read against them.
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10)


class PrometheusAdvisorMetrics:
    def __init__(self, registry: CollectorRegistry, breaker: CircuitBreaker | None = None) -> None:
        self._replies = Counter(
            "advisor_llm_replies_total",
            "Replies recorded by the advisor, answered by the model or degraded (D-02)",
            ["outcome"],
            registry=registry,
        )
        for outcome in ("answered", "degraded"):
            self._replies.labels(outcome=outcome)  # both series exist from the start
        breaker_open = Gauge(
            "advisor_llm_breaker_open",
            "1 while the circuit around the LLM provider is open or half-open, else 0 (ADR-003)",
            registry=registry,
        )
        breaker_open.set_function(lambda: 0 if breaker is None or breaker.state == "closed" else 1)

    def reply_recorded(self, degraded: bool) -> None:
        self._replies.labels(outcome="degraded" if degraded else "answered").inc()
