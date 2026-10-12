"""The circuit breaker around the provider (ADR-003): after 3 consecutive failures the circuit
opens and every reply degrades at once for 60 s; then one call goes through (half-open) and its
result closes or reopens the circuit."""

import time
from collections.abc import Callable
from threading import Lock


class CircuitBreaker:
    def __init__(
        self, threshold: int = 3, reset_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._lock = Lock()
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self._clock() - self.opened_at >= self.reset_seconds else "open"

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            if self._clock() - self.opened_at >= self.reset_seconds:
                self.opened_at = self._clock()  # one trial call; others wait for its result
                self.failures = self.threshold - 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = self._clock()
