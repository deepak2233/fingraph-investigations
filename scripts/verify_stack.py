"""CI smoke test against the running Kafka, PostgreSQL, worker, and API stack."""

import json
import os
import time

import httpx
from sqlalchemy import func, select

from pipeline.cli import fixture_events
from pipeline.contracts import Review
from pipeline.service import InvestigationService
from pipeline.store import Store, audit, cases, events, outbox


def main():
    store = Store(os.environ["DATABASE_URL"])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with store.engine.connect() as connection:
            count = connection.execute(select(func.count()).select_from(cases)).scalar()
            projected = connection.execute(
                select(func.count()).select_from(outbox).where(outbox.c.status == "published")
            ).scalar()
        if count == 5 and projected == 5:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Pipeline did not finish: cases={count}, projected={projected}")
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
                "graph_projections": projected,
                "review": result["status"],
                "audit_records": 6,
                "api": "healthy",
            }
        )
    )


if __name__ == "__main__":
    main()
