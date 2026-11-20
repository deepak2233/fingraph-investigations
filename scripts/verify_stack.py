"""CI smoke test against the running Kafka, PostgreSQL, worker, and API stack."""

import argparse
import json
import os
import time

import httpx
from confluent_kafka import Consumer, TopicPartition
from sqlalchemy import func, select

from pipeline.cli import fixture_events
from pipeline.contracts import Review
from pipeline.service import InvestigationService
from pipeline.store import Store, audit, cases, events, outbox
from pipeline.worker import CONSUMER_GROUP


def verify_offsets():
    """Wait for replay consumption, not just unchanged database counts."""
    topic = os.getenv("KAFKA_TOPIC", "fingraph.cases.v1")
    config = json.loads(os.getenv("KAFKA_CLIENT_CONFIG", "{}"))
    config.update(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": CONSUMER_GROUP,
            "enable.auto.commit": False,
        }
    )
    consumer = Consumer(config)
    try:
        metadata = consumer.list_topics(topic, timeout=10).topics[topic]
        partitions = [TopicPartition(topic, number) for number in metadata.partitions]
        high = {p.partition: consumer.get_watermark_offsets(p, timeout=5)[1] for p in partitions}
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            committed = consumer.committed(partitions, timeout=5)
            if any(high.values()) and all(
                not high[p.partition] or (p.error is None and p.offset >= high[p.partition])
                for p in committed
            ):
                print(json.dumps({"kafka_offsets_committed": high}))
                return
            time.sleep(1)
        raise RuntimeError("The intake group did not commit through the verification watermark")
    finally:
        consumer.close()


def main(intake_only=False, check_offsets=False):
    store = Store(os.environ["DATABASE_URL"])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with store.engine.connect() as connection:
            count = connection.execute(select(func.count()).select_from(cases)).scalar()
            projection_counts = dict(
                connection.execute(select(outbox.c.status, func.count()).group_by(outbox.c.status)).all()
            )
        expected = {"pending": 5} if intake_only else {"published": 5}
        if count == 5 and projection_counts == expected:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Pipeline did not finish: cases={count}, projections={projection_counts}")
    if check_offsets:
        verify_offsets()
    if intake_only:
        print(json.dumps({"cases_committed_without_projector": count, "outbox": projection_counts}))
        return
    service = InvestigationService(store)
    first = service.ingest(fixture_events()[0])
    review = Review(
        action="approve",
        reason="Integration evidence checked",
        expected_version=1,
        request_id="integration-review",
    )
    result = service.review("demo", first["run_id"], "ci-reviewer", review)
    assert result["status"] == "reviewed"
    assert service.review("demo", first["run_id"], "ci-reviewer", review) == result
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(events)).scalar() == 5
        assert connection.execute(select(func.count()).select_from(audit)).scalar() == 6
    response = httpx.get("http://api:8000/api/health", timeout=10)
    response.raise_for_status()
    assert response.json()["durable_service_configured"]
    print(
        json.dumps(
            {
                "events": 5,
                "cases": count,
                "graph_projections": projection_counts["published"],
                "review": result["status"],
                "audit_records": 6,
                "api": "healthy",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake-only", action="store_true")
    parser.add_argument("--check-offsets", action="store_true")
    args = parser.parse_args()
    main(args.intake_only, args.check_offsets)
