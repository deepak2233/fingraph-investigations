"""Build bounded candidate cases from a canonical ledger snapshot.

Upstream connectors supply resolved account IDs and source references. This
entry point evaluates the implemented fan-out and chronological cycle rules.
"""

import argparse
from datetime import timedelta
import json
from pathlib import Path

from pydantic import Field

from api.core import risk_signals
from pipeline.contracts import Identifier, InvestigationCase, CaseEvent
from pipeline.store import digest


class LedgerSnapshot(InvestigationCase):
    tenant_id: Identifier
    lookback_hours: int = Field(default=24, ge=1, le=168)


def build_cases(snapshot):
    snapshot = LedgerSnapshot.model_validate(snapshot)
    case = snapshot.model_dump(mode="json", exclude={"tenant_id", "lookback_hours"})
    cutoff = snapshot.as_of - timedelta(hours=snapshot.lookback_hours)
    case["transactions"] = [
        transaction.model_dump(mode="json")
        for transaction in snapshot.transactions
        if transaction.timestamp >= cutoff
    ]
    if not case["transactions"]:
        return []
    signals = risk_signals(case)
    events = []
    for signal in signals:
        evidence_ids = set(signal["evidence"])
        transactions = [item for item in case["transactions"] if item["id"] in evidence_ids]
        account_ids = {item[field] for item in transactions for field in ("sender", "receiver")}
        # One-hop context is retained within the bounded source snapshot.
        context = [
            item
            for item in case["transactions"]
            if item["sender"] in account_ids or item["receiver"] in account_ids
        ]
        context_accounts = {item[field] for item in context for field in ("sender", "receiver")}
        candidate = {
            **case,
            "title": signal["type"].replace("_", " ").title(),
            "summary": signal["description"],
            "transactions": context,
            "accounts": [item for item in case["accounts"] if item["id"] in context_accounts],
        }
        fingerprint = digest([snapshot.tenant_id, case["as_of"], candidate])
        candidate["id"] = "FG-" + fingerprint[:24]
        events.append(
            CaseEvent(
                schema_version=1,
                event_id=fingerprint,
                tenant_id=snapshot.tenant_id,
                received_at=snapshot.as_of,
                case=candidate,
            ).model_dump(mode="json")
        )
    return events


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        "".join(json.dumps(event) + "\n" for event in build_cases(json.loads(args.input.read_text())))
    )
