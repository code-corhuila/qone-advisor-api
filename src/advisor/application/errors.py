"""Errors of the application layer; the HTTP adapter translates them (403, 404, 503) in one place."""


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
