"""The ready probe (HU-PLT-004, ADR-011): asks every dependency check in order. A check that
raises counts as down: the probe answers, it never fails. Without dependencies (the in-memory
runtime) the advisor is ready."""

import logging
from collections.abc import Iterable

from advisor.application.port.inbound.health import Readiness, ReadinessUseCase
from advisor.application.port.outbound.ports import DependencyCheck

log = logging.getLogger("advisor.health")


class CheckReadiness(ReadinessUseCase):
    def __init__(self, checks: Iterable[DependencyCheck]) -> None:
        self._checks = tuple(checks)

    def check(self) -> Readiness:
        states: dict[str, str] = {}
        for dependency in self._checks:
            try:
                ok = dependency.reachable()
            except Exception as error:  # noqa: BLE001 - a broken check means "not ready", never a 500
                log.warning("readiness check failed", extra={"detail": f"{dependency.name}: {error}"})
                ok = False
            states[dependency.name] = "ok" if ok else "down"
        return Readiness(states)
