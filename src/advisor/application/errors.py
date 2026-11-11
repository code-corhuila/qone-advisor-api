"""Errors of the application layer; the HTTP adapter translates them (403, 404, 422, 429, 503) in one place."""


class Forbidden(Exception):
    def __init__(self, message: str = "this role may not perform the operation") -> None:
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message)
        self.message = message


class ServiceUnavailable(Exception):
    def __init__(self, message: str = "a dependency is unavailable; try again") -> None:
        super().__init__(message)
        self.message = message


class IdempotencyKeyReused(Exception):
    """The owner already used this Idempotency-Key for a different request (#55): nothing is written."""

    def __init__(self, message: str = "the Idempotency-Key was already used for a different request") -> None:
        super().__init__(message)
        self.message = message


class TooManyRequests(Exception):
    """The person spent their message quota (#53); `retry_after` is in whole seconds."""

    def __init__(self, limit: int, retry_after: int) -> None:
        self.message = f"message quota reached: at most {limit} messages per hour"
        super().__init__(self.message)
        self.limit = limit
        self.retry_after = retry_after
