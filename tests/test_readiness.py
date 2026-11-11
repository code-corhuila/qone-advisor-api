"""HU-PLT-004 (code-corhuila/qone-docs#59): `/health/ready` says whether the advisor can take
traffic. Core level: each dependency check is asked, a failing or raising one makes it not
ready. HTTP level: 200 ready, 503 with the common envelope. Adapter: `SELECT 1` on the pool."""

import os
from contextlib import contextmanager

import psycopg
import pytest

from advisor.adapter.outbound.persistence.postgres import PostgresReachable
from advisor.application.usecase.readiness import CheckReadiness
from tests.http_support import make_client

READY = "/api/v1/advisor/health/ready"


class Check:
    def __init__(self, name: str, answer) -> None:
        self.name = name
        self.answer = answer

    def reachable(self) -> bool:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_every_check_ok_is_ready():
    result = CheckReadiness([Check("database", True)]).check()
    assert result.ready and result.checks == {"database": "ok"} and result.unreachable == []


def test_a_failing_or_raising_check_is_not_ready():
    result = CheckReadiness([Check("database", False), Check("other", RuntimeError("boom"))]).check()
    assert not result.ready and result.checks == {"database": "down", "other": "down"}
    assert result.unreachable == ["database", "other"]


def test_without_dependencies_the_in_memory_runtime_is_ready():
    assert CheckReadiness([]).check().ready


def test_ready_answers_200_with_the_checks():
    client, *_ = make_client()
    r = client.get(READY)
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "service": "qone-advisor-api", "checks": {}}


def test_ready_answers_503_with_the_envelope_when_the_database_is_down():
    client, *_ = make_client(readiness=CheckReadiness([Check("database", False)]))
    r = client.get(READY, headers={"X-Correlation-Id": "ready-probe-1"})
    assert r.status_code == 503
    assert r.json() == {
        "error": "SERVICE_UNAVAILABLE",
        "message": "database is not reachable",
        "traceId": "ready-probe-1",
    }
    assert client.get("/api/v1/advisor/health").status_code == 200, "liveness does not change"


class DownPool:
    """A pool whose server is gone: getting a connection fails like psycopg does."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    @contextmanager
    def connection(self, timeout=None):
        self.timeouts.append(timeout)
        raise psycopg.OperationalError("connection refused")
        yield


def test_select_1_on_a_pool_that_cannot_connect_is_not_reachable_and_waits_briefly():
    pool = DownPool()
    check = PostgresReachable(pool, timeout_seconds=1.0)
    assert check.name == "database" and check.reachable() is False and pool.timeouts == [1.0]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_select_1_against_the_real_database_is_reachable():
    from advisor.adapter.outbound.persistence.postgres import connect

    pool = connect(os.environ["TEST_DATABASE_URL"], max_size=1)
    try:
        assert PostgresReachable(pool).reachable() is True
    finally:
        pool.close()
