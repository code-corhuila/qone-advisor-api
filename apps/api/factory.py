"""The composition root (Annex C): the only module that knows every concrete type and every
limit. It reads the environment, builds the adapters and hands the ports to the application."""

import json
import logging
import os
import sys

from advisor.adapter.inbound.http.app import create_app
from advisor.adapter.inbound.http.auth import TokenVerifier
from advisor.adapter.outbound.llm.breaker import CircuitBreaker
from advisor.adapter.outbound.persistence.memory import InMemoryAdvisor
from advisor.adapter.outbound.runtime import SystemClock, Uuid4Generator
from advisor.adapter.outbound.saga.http import HttpSagaStarter
from advisor.application.port.outbound.ports import SagaUnavailable
from advisor.application.usecase.advisor import AdvisorService

# Explicit limits (norm 5.3.10, Annex C), declared here with their values.
DB_POOL_MAX = 10
DB_WAIT_SECONDS = 5.0
DB_STATEMENT_TIMEOUT_MS = 5000
HTTP_KEEP_ALIVE_SECONDS = 60
HTTP_GRACEFUL_SHUTDOWN_SECONDS = 20
LLM_BUDGET_SECONDS = 120.0  # one reply, every round included (ADR-003)
BREAKER_FAILURES, BREAKER_RESET_SECONDS = 3, 60.0
SAGA_ATTEMPTS = 3


class Settings:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.database_url = env.get("ADVISOR_DATABASE_URL", "").strip()
        self.public_key = _public_key(env)
        self.current_term = env.get("CURRENT_TERM", "2026-B").strip()
        self.openai_api_key = env.get("OPENAI_API_KEY", "").strip()
        self.openai_model = env.get("OPENAI_MODEL", "gpt-4o-mini").strip()
        self.workflow_url = env.get("WORKFLOW_URL", "http://qone-workflow:8000").strip()
        self.service_token = env.get("SERVICE_TOKEN", "").strip()
        self.port = int(env.get("PORT", "8000"))
        self.log_level = env.get("LOG_LEVEL", "info").lower()


def _public_key(env: dict[str, str]) -> str:
    """JWT_PUBLIC_KEY as the PEM in one line with literal \\n, or JWT_PUBLIC_KEY_FILE."""
    inline = env.get("JWT_PUBLIC_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = env.get("JWT_PUBLIC_KEY_FILE", "").strip()
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    raise SystemExit("JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_FILE is required: the service validates every token itself")


class _NoSaga:
    """Without SERVICE_TOKEN a confirmation cannot start the saga: it answers 503 and says why."""

    def start(self, *args, **kwargs):
        raise SagaUnavailable("SERVICE_TOKEN is not configured")


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("advisor")
    pool = None
    if settings.database_url and settings.database_url != "memory":
        from advisor.adapter.outbound.persistence.postgres import PostgresAdvisorStore, PostgresProjections, connect

        pool = connect(settings.database_url, DB_POOL_MAX, DB_WAIT_SECONDS, DB_STATEMENT_TIMEOUT_MS)
        store, projections = PostgresAdvisorStore(pool), PostgresProjections(pool)
        log.info("store: postgresql (pool max %s, statement timeout %s ms)", DB_POOL_MAX, DB_STATEMENT_TIMEOUT_MS)
    else:
        store = projections = InMemoryAdvisor()
        log.warning("store: in memory and empty (ADVISOR_DATABASE_URL not set); development only")
    agent = None
    if settings.openai_api_key:
        from advisor.adapter.outbound.llm.cache import PostgresLlmCache
        from advisor.adapter.outbound.llm.openai_agent import OpenAIAgent

        breaker = CircuitBreaker(BREAKER_FAILURES, BREAKER_RESET_SECONDS)
        cache = PostgresLlmCache(pool) if pool else None
        agent = OpenAIAgent(settings.openai_model, settings.openai_api_key, breaker, cache, LLM_BUDGET_SECONDS)
        log.info(
            "llm: %s, budget %ss, breaker %s failures / %ss",
            settings.openai_model,
            LLM_BUDGET_SECONDS,
            BREAKER_FAILURES,
            BREAKER_RESET_SECONDS,
        )
    else:
        log.warning("llm: missing (OPENAI_API_KEY not set); every reply is degraded (D-02)")
    if settings.service_token:
        saga = HttpSagaStarter(settings.workflow_url, settings.service_token, attempts=SAGA_ATTEMPTS)
    else:
        saga = _NoSaga()
        log.warning("saga: SERVICE_TOKEN not set; confirming a plan answers 503")
    service = AdvisorService(store, projections, agent, saga, SystemClock(), Uuid4Generator(), settings.current_term)
    return create_app(service, TokenVerifier(settings.public_key))


class JsonFormatter(logging.Formatter):
    """One JSON line per record with the traceId (norm 5.3.9)."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("traceId", "method", "path", "status", "durationMs", "detail", "event", "applied"):
            if hasattr(record, key):
                line[key] = getattr(record, key)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
