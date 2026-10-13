"""The clock and the id generator of the running service: aware UTC instants and UUID v4."""

from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid4Generator:
    def new_id(self) -> UUID:
        return uuid4()
