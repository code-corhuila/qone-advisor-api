"""The client of the enrollment saga (WF-02 of qone-workflow) with the advisor's SERVICE token,
never the student's (D-08): `origin: AGENT`, the student's id and the confirmation id
(INV-ENR-005), `Idempotency-Key: advisor-plan-<planId>` so a retry never starts a second saga,
and the caller's X-Correlation-Id. Retries only on network errors, 429 and 5xx, bounded, with
full jitter (Annex D); any other answer is final and reported as unavailable."""

import logging
import random
import time
from collections.abc import Callable
from uuid import UUID

import httpx

from advisor.application.port.outbound.ports import SagaResult, SagaUnavailable

RETRYABLE = {429, 500, 502, 503, 504}
log = logging.getLogger("advisor.saga")


class HttpSagaStarter:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        client: httpx.Client | None = None,
        attempts: int = 3,
        base_delay: float = 0.2,
        max_delay: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/sagas/enroll-student"
        self._token = service_token
        # The saga answers when it ends: three participants, each with its own retries.
        self._client = client or httpx.Client(timeout=httpx.Timeout(45.0, connect=3.0))
        self._attempts = attempts
        self._base = base_delay
        self._cap = max_delay
        self._sleep = sleep

    def start(
        self, student_id: UUID, section_id: UUID, confirmation_id: UUID, idempotency_key: str, correlation_id: str
    ) -> SagaResult:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": idempotency_key,
            "X-Correlation-Id": correlation_id or idempotency_key,
        }
        body = {
            "studentId": str(student_id),
            "sectionId": str(section_id),
            "origin": "AGENT",
            "confirmationId": str(confirmation_id),
        }
        last = "no attempt made"
        for attempt in range(self._attempts):
            try:
                response = self._client.post(self._url, json=body, headers=headers)
            except httpx.TransportError as error:
                last = f"{type(error).__name__}"
            else:
                if response.status_code in (200, 201):
                    saga = response.json()
                    return SagaResult(UUID(saga["id"]), saga["status"], saga.get("reason"))
                if response.status_code not in RETRYABLE:
                    log.error(
                        "the saga refused the start", extra={"detail": f"{response.status_code} {response.text[:300]}"}
                    )
                    raise SagaUnavailable(f"unexpected {response.status_code}")
                last = str(response.status_code)
            if attempt + 1 < self._attempts:
                self._sleep(random.uniform(0, min(self._cap, self._base * 2**attempt)))  # noqa: S311 - jitter
        raise SagaUnavailable(last)
