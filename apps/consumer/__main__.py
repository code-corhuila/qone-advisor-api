"""`python -m apps.consumer`: the event consumer of the advisor, a second process of
the same image (the API keeps answering while the projections move). Reconnects after a lost
broker with a pause."""

import logging
import os
import time

from advisor.adapter.inbound.events.rabbitmq import consume
from advisor.adapter.outbound.persistence.postgres import connect
from advisor.adapter.outbound.persistence.projections import PostgresProjectionStore
from advisor.application.usecase.projections import ProjectEvents
from apps.api.factory import DB_POOL_MAX, DB_STATEMENT_TIMEOUT_MS, DB_WAIT_SECONDS, configure_logging


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "info"))
    logging.getLogger("pika").setLevel(logging.WARNING)
    log = logging.getLogger("advisor.consumer")
    database, broker = os.environ["ADVISOR_DATABASE_URL"], os.environ["RABBITMQ_URL"]
    project = ProjectEvents(
        PostgresProjectionStore(connect(database, DB_POOL_MAX, DB_WAIT_SECONDS, DB_STATEMENT_TIMEOUT_MS)),
        os.environ.get("CURRENT_TERM", "2026-B").strip(),
    )
    while True:
        try:
            log.info("consuming catalog.#, billing.# and enrollment.# into the projections and plans")
            consume(broker, project)
        except Exception:  # noqa: BLE001 - a lost broker is retried; the queue keeps the messages
            log.exception("consumer stopped; reconnecting in 5 s")
            time.sleep(5)


if __name__ == "__main__":
    main()
