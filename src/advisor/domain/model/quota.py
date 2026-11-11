"""The message quota of a person (security P0, code-corhuila/qone-docs#53, threat D on TB6): at most
`per_hour` messages in a sliding hour across all their conversations. Past it, the person waits
until their oldest message of the hour leaves the window. The rule is pure: the caller counts the
messages and hands their oldest instant."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class MessageQuota:
    per_hour: int

    def __post_init__(self) -> None:
        if self.per_hour < 1:
            raise ValueError("the message quota must be at least 1 per hour")

    @staticmethod
    def window_start(now: datetime) -> datetime:
        """Messages sent after this instant count against the quota."""
        return now - WINDOW

    def retry_after(self, sent: int, oldest: datetime | None, now: datetime) -> int | None:
        """None while `sent` is under the quota; else the whole seconds (at least 1) until the oldest
        message of the window leaves it."""
        if sent < self.per_hour or oldest is None:
            return None
        return max(1, math.ceil((oldest + WINDOW - now).total_seconds()))
