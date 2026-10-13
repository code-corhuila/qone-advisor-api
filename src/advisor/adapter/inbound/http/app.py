"""The FastAPI application of the advisor: correlation and access log (norm 5.3.9),
the error envelope, the routes."""

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request

from advisor.adapter.inbound.http import errors
from advisor.adapter.inbound.http.auth import TokenVerifier
from advisor.adapter.inbound.http.router import router
from advisor.application.port.inbound.advisor import AdvisorUseCases

SERVICE = "qone-advisor-api"
VERSION = "2.0.0"
CORRELATION_HEADER = "X-Correlation-Id"
access = logging.getLogger("advisor.access")


def create_app(use_cases: AdvisorUseCases, verifier: TokenVerifier) -> FastAPI:
    app = FastAPI(title=SERVICE, version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.use_cases = use_cases
    app.state.verifier = verifier
    app.state.service = SERVICE
    app.state.version = VERSION

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        received = request.headers.get(CORRELATION_HEADER, "").strip()
        correlation_id = received[:128] if received else str(uuid4())
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:  # noqa: BLE001 - the last line: neutral 500, full detail in the log
            response = errors.internal_error(request, error)
        response.headers[CORRELATION_HEADER] = correlation_id
        access.info(
            "request",
            extra={
                "traceId": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "durationMs": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    errors.install(app)
    app.include_router(router)
    return app
