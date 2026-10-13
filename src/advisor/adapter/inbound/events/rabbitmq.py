"""The inbound adapter of events: a durable queue `advisor.projections` bound to `catalog.#`,
`billing.#` and `enrollment.#` on `qampus.events`, manual acknowledgement after the change is committed. A message
that fails is requeued after a short pause; one that cannot be read is logged and dropped."""

import json
import logging
import time
from datetime import datetime
from uuid import UUID

import pika

from advisor.application.usecase.projections import IncomingEvent, ProjectEvents

EXCHANGE = "qampus.events"
QUEUE = "advisor.projections"
BINDINGS = ("catalog.#", "billing.#", "enrollment.#")
log = logging.getLogger("advisor.consumer")


def parse(body: bytes) -> IncomingEvent:
    m = json.loads(body)
    return IncomingEvent(
        UUID(m["eventId"]), m["eventType"], m["payload"], datetime.fromisoformat(m["occurredAt"].replace("Z", "+00:00"))
    )


def consume(url: str, project: ProjectEvents) -> None:
    channel = pika.BlockingConnection(pika.URLParameters(url)).channel()
    channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    channel.queue_declare(queue=QUEUE, durable=True)
    for key in BINDINGS:
        channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=key)
    channel.basic_qos(prefetch_count=10)

    def on_message(ch, method, properties, body):
        correlation = properties.correlation_id or ""
        try:
            event = parse(body)
        except (ValueError, KeyError) as error:
            log.error("unreadable event dropped", extra={"traceId": correlation, "detail": str(error)})
            ch.basic_ack(method.delivery_tag)
            return
        try:
            project.handle(event, correlation)
        except Exception:  # noqa: BLE001 - the change was not committed: try again shortly
            log.exception("event not applied; requeued", extra={"traceId": correlation})
            time.sleep(1)
            ch.basic_nack(method.delivery_tag, requeue=True)
            return
        ch.basic_ack(method.delivery_tag)

    channel.basic_consume(queue=QUEUE, on_message_callback=on_message)
    channel.start_consuming()
