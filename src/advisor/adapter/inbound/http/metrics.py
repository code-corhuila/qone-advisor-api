"""RED metrics of the HTTP border (HU-OBS-001, ADR-010): every request counted by method, route
template and status class, its duration in a histogram, and `GET /metrics` at the root of the
app for Prometheus. The gateway never routes `/metrics`; Prometheus scrapes the container on the
platform network. The route label is the matched template
(`/api/v1/advisor/conversations/{conversation_id}/messages`) or `unmatched`, never the raw path,
so the series stay bounded; likewise a non-standard method is counted as `OTHER`."""

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from advisor.adapter.outbound.metrics.prometheus import LATENCY_BUCKETS

METRICS_PATH = "/metrics"
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
OTHER_METHOD = "OTHER"


def method_of(request: Request) -> str:
    """The HTTP method, or `OTHER` for a non-standard one: the client chooses the method freely, so
    the label would otherwise be unbounded too."""
    return request.method if request.method in METHODS else OTHER_METHOD


def install(app: FastAPI, registry: CollectorRegistry, service: str) -> None:
    """Adds the middleware and the scrape route. Call it after the other middlewares so this one
    is the outermost and sees the final status, the neutral 500 included."""
    requests = Counter(
        "http_server_requests_total",
        "HTTP requests by service, method, route template and status class",
        ["service", "method", "route", "status_class"],
        registry=registry,
    )
    duration = Histogram(
        "http_server_request_duration_seconds",
        "HTTP request duration in seconds by service, method and route template",
        ["service", "method", "route"],
        buckets=LATENCY_BUCKETS,
        registry=registry,
    )
    app.state.metrics_registry = registry

    @app.middleware("http")
    async def measure(request: Request, call_next):
        if request.url.path == METRICS_PATH:
            return await call_next(request)
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        method = method_of(request)
        requests.labels(service, method, template, f"{response.status_code // 100}xx").inc()
        duration.labels(service, method, template).observe(time.perf_counter() - started)
        return response

    @app.get(METRICS_PATH, include_in_schema=False)
    def scrape() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
