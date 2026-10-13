"""`python -m apps.api`: the service with its explicit limits (norm 5.3.10)."""

import uvicorn

from apps.api.factory import HTTP_GRACEFUL_SHUTDOWN_SECONDS, HTTP_KEEP_ALIVE_SECONDS, Settings, build_app


def main() -> None:
    settings = Settings()
    uvicorn.run(
        build_app(settings),
        host="0.0.0.0",  # noqa: S104 - reachable only on the platform network; nothing is published (norm 5.6.1)
        port=settings.port,
        timeout_keep_alive=HTTP_KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=HTTP_GRACEFUL_SHUTDOWN_SECONDS,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
