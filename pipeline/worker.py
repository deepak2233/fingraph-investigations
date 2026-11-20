"""Kafka at-least-once intake. SQL commits precede source offset commits."""

import json
import logging
import os
import signal

from confluent_kafka import Consumer, KafkaError
from pydantic import ValidationError

from pipeline.contracts import CaseEvent
from pipeline.service import InvalidEvidence, InvestigationService
from pipeline.store import Conflict, Store, digest

logger = logging.getLogger("fingraph.worker")
CONSUMER_GROUP = "fingraph-ingestion-v1"


class InvalidRecord(ValueError):
    """Kafka record cannot be interpreted as a bounded CaseEvent."""


def decode_record(raw):
    if not raw:
        raise InvalidRecord("Missing Kafka payload")
    if len(raw) > 2_000_000:
        raise InvalidRecord("Event exceeds 2 MB")
    try:
        return CaseEvent.model_validate_json(raw)
    except ValidationError as exc:
        raise InvalidRecord("Event violates the CaseEvent contract") from exc


def handle_message(service, message):
    raw = message.value()
    coordinates = f"{message.topic()}:{message.partition()}:{message.offset()}"
    try:
        service.ingest(decode_record(raw))
    except (InvalidRecord, InvalidEvidence, Conflict) as exc:
        # Malformed records are retained for inspection before acknowledging Kafka.
        # Database or dependency failures propagate, leaving the offset uncommitted.
        service.store.quarantine_record(
            digest(coordinates),
            "ingestion",
            coordinates,
            (raw or b"")[:2_000_000].decode("utf-8", errors="replace"),
            str(exc),
        )
        logger.warning(
            "event_quarantined", extra={"source": coordinates, "error_type": type(exc).__name__}
        )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(os.environ["DATABASE_URL"])
    service = InvestigationService(store)
    config = json.loads(os.getenv("KAFKA_CLIENT_CONFIG", "{}"))
    config.update(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": CONSUMER_GROUP,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 300000,
        }
    )
    consumer = Consumer(config)
    consumer.subscribe([os.getenv("KAFKA_TOPIC", "fingraph.cases.v1")])
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            message = consumer.poll(1.0)
            if message is not None:
                if message.error():
                    if message.error().code() != KafkaError._PARTITION_EOF:
                        raise RuntimeError(str(message.error()))
                else:
                    handle_message(service, message)
                    offsets = consumer.commit(message=message, asynchronous=False)
                    for offset in offsets:
                        if offset.error is not None:
                            raise RuntimeError(f"Kafka offset commit failed: {offset.error}")
    finally:
        consumer.close()
        store.engine.dispose()


if __name__ == "__main__":
    main()
