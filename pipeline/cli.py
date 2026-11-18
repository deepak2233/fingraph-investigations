"""Explicit schema initialization, JSONL replay, and demo event publication."""

import argparse
import json
import os
from pathlib import Path

from api.core import CASES
from pipeline.neptune import FileProjection, project_pending
from pipeline.service import InvestigationService
from pipeline.store import Store


def fixture_events():
    return [
        {
            "schema_version": 1,
            "event_id": "fixture-" + case["id"],
            "tenant_id": "demo",
            "received_at": case["as_of"],
            "case": case,
        }
        for case in CASES
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init-db", "replay", "publish"])
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    if args.command == "publish":
        from confluent_kafka import Producer

        config = json.loads(os.getenv("KAFKA_CLIENT_CONFIG", "{}"))
        config.update(
            {
                "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
                "enable.idempotence": True,
            }
        )
        producer = Producer(config)
        errors = []
        for payload in fixture_events():
            producer.produce(
                os.getenv("KAFKA_TOPIC", "fingraph.cases.v1"),
                key=payload["tenant_id"] + ":" + payload["case"]["id"],
                value=json.dumps(payload),
                on_delivery=lambda error, _: errors.append(str(error)) if error else None,
            )
        remaining = producer.flush(30)
        if errors or remaining:
            raise RuntimeError(f"Kafka delivery failed: {errors}, remaining={remaining}")
        print(json.dumps({"published": len(CASES)}))
        return
    store = Store(os.getenv("DATABASE_URL", "sqlite:///var/fingraph.db"))
    if args.command == "init-db":
        store.initialize()
        print("Schema ready")
        return
    payloads = (
        (json.loads(line) for line in args.file.read_text().splitlines() if line.strip())
        if args.file
        else fixture_events()
    )
    service = InvestigationService(store)
    for payload in payloads:
        result = service.ingest(payload)
        print(
            json.dumps(
                {"case_id": result["case_id"], "run_id": result["run_id"], "status": result["status"]}
            )
        )
    print(json.dumps({"projected": project_pending(store, FileProjection("var/graphs"))}))


if __name__ == "__main__":
    main()
