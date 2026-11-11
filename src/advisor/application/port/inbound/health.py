"""What the ready probe offers (HU-PLT-004): whether every dependency answers, by name."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Readiness:
    checks: dict[str, str]  # dependency -> "ok" | "down"

    @property
    def ready(self) -> bool:
        return all(state == "ok" for state in self.checks.values())

    @property
    def unreachable(self) -> list[str]:
        return [name for name, state in self.checks.items() if state != "ok"]


class ReadinessUseCase(Protocol):
    def check(self) -> Readiness: ...
