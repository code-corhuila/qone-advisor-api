"""The single error envelope (norm 5.3.5, Annex C) and the ONE place where a domain or
application error becomes a status code; unknown routes and malformed JSON included."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from advisor.adapter.inbound.http.auth import AuthError
from advisor.application.errors import Forbidden, NotFound, ServiceUnavailable
from advisor.domain.model.errors import BusinessRuleViolation, FieldError, InvalidStatusTransition, ValidationError

log = logging.getLogger("advisor.http")


def envelope(
    request: Request, status: int, code: str, message: str, details: list[FieldError] | None = None
) -> JSONResponse:
    body: dict = {"error": code, "message": message, "traceId": trace_id(request)}
    if details:
        body["details"] = [{"field": d.field, "message": d.message} for d in details]
    return JSONResponse(body, status_code=status)


def trace_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "")


def install(app: FastAPI) -> None:
    @app.exception_handler(ValidationError)
    async def _validation(request: Request, error: ValidationError):
        return envelope(request, 400, "VALIDATION_ERROR", error.message, error.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, error: RequestValidationError):
        details = [FieldError(_field_of(e.get("loc", ())), str(e.get("msg", "invalid"))) for e in error.errors()]
        return envelope(request, 400, "VALIDATION_ERROR", "the request has invalid fields", details)

    @app.exception_handler(AuthError)
    async def _unauthorized(request: Request, error: AuthError):
        return envelope(request, 401, "UNAUTHORIZED", error.message)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, error: Forbidden):
        return envelope(request, 403, "FORBIDDEN", error.message)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, error: NotFound):
        return envelope(request, 404, "NOT_FOUND", error.message)

    @app.exception_handler(BusinessRuleViolation)
    async def _business_rule(request: Request, error: BusinessRuleViolation):
        return envelope(request, 422, "BUSINESS_RULE_VIOLATION", error.message)

    @app.exception_handler(InvalidStatusTransition)
    async def _transition(request: Request, error: InvalidStatusTransition):
        return envelope(request, 422, "INVALID_STATUS_TRANSITION", error.message)

    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(request: Request, error: ServiceUnavailable):
        return envelope(request, 503, "SERVICE_UNAVAILABLE", error.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException):
        if error.status_code in (404, 405):
            return envelope(request, 404, "NOT_FOUND", "resource not found")
        log.error("unexpected http exception", extra={"traceId": trace_id(request), "detail": str(error.detail)})
        return envelope(request, 500, "INTERNAL_ERROR", "unexpected error")


def internal_error(request: Request, error: Exception) -> JSONResponse:
    log.exception("unhandled error", extra={"traceId": trace_id(request)}, exc_info=error)
    return envelope(request, 500, "INTERNAL_ERROR", "unexpected error")


def _field_of(loc: tuple) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) if parts else (str(loc[0]) if loc else "body")
