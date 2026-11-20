"""Compare point-in-time features and hand selected synthetic cases to FinGraph."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.history_ablation import verify_file
from ml.prepare import COLUMNS, EXPECTED_SHA256
from ml.rolling_features import BEHAVIOR_FEATURES, DAY_MINUTES, NETWORK_FEATURES, rolling_features
from ml.train import edge_features, gradient_boosting_baseline
from pipeline.contracts import CaseEvent
from pipeline.service import InvestigationService
from pipeline.store import Store, digest

# Each tuple gives train start, validation start, test start and test end.
FOLDS = (
    ("2022-09-03", "2022-09-06", "2022-09-07", "2022-09-09"),
    ("2022-09-03", "2022-09-08", "2022-09-09", "2022-09-11"),
)
PAYMENT_CODES = {
    "Australian Dollar": "AUD",
    "Bitcoin": "XBT",
    "Brazil Real": "BRL",
    "Canadian Dollar": "CAD",
    "Euro": "EUR",
    "Mexican Peso": "MXN",
    "Ruble": "RUB",
    "Rupee": "INR",
    "Saudi Riyal": "SAR",
    "Shekel": "ILS",
    "Swiss Franc": "CHF",
    "UK Pound": "GBP",
    "US Dollar": "USD",
    "Yen": "JPY",
    "Yuan": "CNY",
}
PREVIEW_CASES = 3
PREVIEW_TRANSFERS = 100


def read_ledger(source):
    verify_file(source, EXPECTED_SHA256)
    categories = (
        "From Bank",
        "To Bank",
        "Account",
        "Account.1",
        "Payment Currency",
        "Receiving Currency",
        "Payment Format",
    )
    dtype = dict.fromkeys(categories, "category")
    dtype.update({"Amount Paid": "float64", "Amount Received": "float64", "Is Laundering": "int8"})
    ledger = pd.read_csv(source, dtype=dtype, float_precision="round_trip")
    if list(ledger.columns) != COLUMNS:
        raise ValueError("Unexpected AMLworld source columns")
    minute = pd.to_datetime(ledger.pop("Timestamp"), format="%Y/%m/%d %H:%M", utc=True)
    ledger["minute"] = minute.to_numpy(dtype="datetime64[m]").astype(np.int64)
    ledger["source_row"] = np.arange(2, len(ledger) + 2, dtype=np.int32)
    ledger.rename(
        columns={"Amount Paid": "paid", "Amount Received": "received", "Is Laundering": "label"},
        inplace=True,
    )
    amounts = ledger[["paid", "received"]].to_numpy()
    if not np.isfinite(amounts).all() or (amounts < 0).any() or not ledger.label.isin([0, 1]).all():
        raise ValueError("Unexpected source amount or label")
    for debit, credit in (("From Bank", "To Bank"), ("Payment Currency", "Receiving Currency")):
        shared = pd.CategoricalDtype(ledger[debit].cat.categories.union(ledger[credit].cat.categories))
        ledger[debit], ledger[credit] = ledger[debit].astype(shared), ledger[credit].astype(shared)
    sender = ledger["From Bank"].astype(str).str.cat(ledger.pop("Account").astype(str), sep=":")
    receiver = ledger["To Bank"].astype(str).str.cat(ledger.pop("Account.1").astype(str), sep=":")
    codes, accounts = pd.factorize(pd.concat([sender, receiver], ignore_index=True))
    ledger["source"], ledger["target"] = codes.reshape(2, len(ledger)).astype(np.int32)
    ledger.sort_values("minute", kind="stable", ignore_index=True, inplace=True)
    return ledger, accounts


def case_groups(frame):
    minute, sender = frame.minute.to_numpy(), frame.source.to_numpy()
    day = minute // DAY_MINUTES
    n_days = day[-1] - day[0] + 1
    _, first, group = np.unique(
        sender.astype(np.int64) * n_days + (day - day[0]), return_inverse=True, return_index=True
    )
    # Use the first chronological row as the deterministic case tie-break.
    ordered = np.argsort(first)
    group = np.argsort(ordered)[group]
    labels = np.zeros(len(first), dtype=np.int8)
    np.maximum.at(labels, group, frame.label.to_numpy())
    return group, labels


def case_scores(scores, group, count):
    maximum = np.full(count, -np.inf)
    np.maximum.at(maximum, group, scores)
    return maximum


def measure(labels, scores, budget):
    budget = min(budget, len(labels))
    positives = int(labels.sum())
    order = np.argsort(-scores, kind="stable")[:budget]
    hits = int(labels[order].sum())
    return {
        "population": len(labels),
        "positive_labels": positives,
        "average_precision": (
            float(average_precision_score(labels, scores)) if 0 < positives < len(labels) else None
        ),
        "reviewed": budget,
        "positives_found": hits,
        "labelled_negatives_reviewed": budget - hits,
        "positives_missed": positives - hits,
        "precision": hits / budget,
        "recall": hits / positives if positives else None,
    }


def stamp(minute):
    return datetime.fromtimestamp(int(minute) * 60, timezone.utc).isoformat()


def account_key(account):
    return sha256(str(account).encode()).hexdigest()[:24]


def preview_investigations(frame, accounts, group, scores, ranked, model):
    store = Store("sqlite://")
    try:
        store.initialize()
        return _ingest_preview(
            frame, accounts, group, scores, ranked, model, InvestigationService(store)
        )
    finally:
        store.engine.dispose()


def _ingest_preview(frame, accounts, group, scores, ranked, model, service):
    results = []
    for number in ranked[:PREVIEW_CASES]:
        offsets = np.flatnonzero(group == number)
        anchor = offsets[np.argmax(scores[offsets])]
        evidence = np.r_[anchor, offsets[offsets != anchor][: PREVIEW_TRANSFERS - 1]]
        rows = frame.iloc[np.sort(evidence)]
        actors = set(rows.source) | set(rows.target)
        ids = {code: account_key(accounts[code]) for code in actors}
        day_end = (int(frame.iloc[anchor].minute) // DAY_MINUTES + 1) * DAY_MINUTES
        day = stamp(day_end - DAY_MINUTES)[:10]
        case_id = "shadow-" + account_key(
            f"{EXPECTED_SHA256}:{accounts[frame.iloc[anchor].source]}:{day}"
        )
        events = [
            {
                "id": f"payment-{row.source_row}",
                "sender": "account-" + ids[row.source],
                "receiver": "account-" + ids[row.target],
                "timestamp": stamp(row.minute),
                "currency": PAYMENT_CODES[row.pay_currency],
                "amount": str(row.paid),
                "source_id": f"amlworld-hi-small:row:{row.source_row}",
            }
            for row in rows.rename(columns={"Payment Currency": "pay_currency"}).itertuples(index=False)
        ]
        event = CaseEvent.model_validate(
            {
                "schema_version": 1,
                "event_id": digest([case_id, model, events]),
                "tenant_id": "amlworld-shadow",
                "received_at": stamp(day_end),
                "case": {
                    "id": case_id,
                    "title": "AMLworld shadow ranking",
                    "summary": f"Research account-day selected by {model} at the held-out review budget.",
                    "data_origin": "Synthetic AMLworld debit-side transfer records with placeholder holders",
                    "as_of": stamp(day_end),
                    "accounts": [
                        {"id": "account-" + key, "name": "Synthetic account", "holder": "holder-" + key}
                        for key in sorted(ids.values())
                    ],
                    "transactions": events,
                },
            }
        )
        outcome = service.ingest(event)
        evidence_ids = {row["source"] for row in outcome.get("evidence", [])}
        if outcome["status"] != "awaiting_analyst_review" or evidence_ids != {
            event["source_id"] for event in events
        }:
            raise RuntimeError(f"{case_id}: shadow handoff did not preserve all transfer evidence")
        replay = service.ingest(event)
        audit = service.store.audit_trail(event.tenant_id, outcome["run_id"])
        if replay != outcome or len(audit) != 1:
            raise RuntimeError(f"{case_id}: replay did not return its saved investigation")
        results.append(
            {
                "case_id": case_id,
                "anchor_source_row": int(frame.iloc[anchor].source_row),
                "ranking_score": float(scores[anchor]),
                "account_day_transfers": len(offsets),
                "evidence_transfers": len(events),
                "status": outcome["status"],
                "shacl_conforms": outcome["validation"]["conforms"],
                "audit_records_after_replay": len(audit),
            }
        )
    return results


def benchmark_fold(ledger, history, accounts, dates, transaction_budget, case_budget, final=False):
    bounds = np.asarray(dates, dtype="datetime64[m]").astype(np.int64)
    start, validation, test, end = map(int, np.searchsorted(ledger.minute.to_numpy(), bounds))
    stop = len(ledger) if final else end
    frame = ledger.iloc[start:stop]
    validation, test, end = validation - start, test - start, end - start
    choices = {
        column: sorted(frame.iloc[:validation][column].unique())
        for column in ("Payment Currency", "Payment Format")
    }
    basic, _ = edge_features(frame, choices)
    labels = frame.label.to_numpy()
    subsets = {"validation": slice(validation, test), "test": slice(test, end)}
    if final:
        subsets["sparse_tail"] = slice(end, len(frame))
    groups = {name: case_groups(frame.iloc[part]) for name, part in subsets.items()}
    if any(
        np.unique(labels[part]).size != 2 or np.unique(groups[name][1]).size != 2
        for name, part in subsets.items()
        if name != "sparse_tail"
    ):
        raise ValueError("Each comparison split requires positive and negative labels")
    behavior = history[start:stop, : len(BEHAVIOR_FEATURES)]
    models = {}
    validation_best = -1.0
    selected_model = ""
    selected_test_scores = None
    for name, additional in (
        ("logistic_transaction", None),
        ("boosting_transaction", None),
        ("boosting_behavior", behavior),
        ("boosting_graph", history[start:stop]),
    ):
        print(f"Training {name} for the {dates[2]} test window", flush=True)
        x = basic if additional is None else np.column_stack((basic, additional))
        estimator = (
            make_pipeline(StandardScaler(), LogisticRegression(max_iter=300, class_weight="balanced"))
            if name == "logistic_transaction"
            else gradient_boosting_baseline()
        )
        estimator.fit(x[:validation], labels[:validation])
        results = {}
        for key, part in subsets.items():
            scores = estimator.predict_proba(x[part])[:, 1]
            group, case_labels = groups[key]
            results[key] = {
                "transactions": measure(labels[part], scores, transaction_budget),
                "account_days": measure(
                    case_labels, case_scores(scores, group, len(case_labels)), case_budget
                ),
            }
            if (
                key == "validation"
                and results[key]["account_days"]["average_precision"] > validation_best
            ):
                validation_best = results[key]["account_days"]["average_precision"]
                selected_model = name
            if key == "test" and name == selected_model:
                selected_test_scores = scores
        models[name] = results
    report = {
        "boundaries_utc": list(dates),
        "training_rows": validation,
        "training_positives": int(labels[:validation].sum()),
        "selected_on_validation_case_average_precision": selected_model,
        "models": models,
    }
    if final:
        group, case_labels = groups["test"]
        rank = np.argsort(-case_scores(selected_test_scores, group, len(case_labels)), kind="stable")
        report["shadow_handoff"] = preview_investigations(
            frame.iloc[test:end],
            accounts,
            group,
            selected_test_scores,
            rank[:case_budget],
            selected_model,
        )
    return report


def benchmark(source, transaction_budget, case_budget):
    ledger, accounts = read_ledger(source)
    currencies, _ = pd.factorize(
        pd.concat([ledger["Payment Currency"], ledger["Receiving Currency"]], ignore_index=True)
    )
    pay_currency, receive_currency = currencies.reshape(2, len(ledger)).astype(np.int32)
    print(f"Replaying {len(ledger):,} transfers in event-minute order", flush=True)
    history = rolling_features(
        minutes=ledger.minute.to_numpy(),
        sender=ledger.source.to_numpy(),
        receiver=ledger.target.to_numpy(),
        paid=ledger.paid.to_numpy(),
        received=ledger.received.to_numpy(),
        pay_currency=pay_currency,
        receive_currency=receive_currency,
    )
    return {
        "source": "IBM AMLworld HI-Small",
        "source_sha256": EXPECTED_SHA256,
        "source_rows": len(ledger),
        "source_positives": int(ledger.label.sum()),
        "software": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "history_policy": "All prior event minutes, with simultaneous transactions read before any update",
        "case_policy": "One sender account and UTC day, max transfer score and any labelled outgoing positive",
        "behavior_features": BEHAVIOR_FEATURES,
        "network_features": NETWORK_FEATURES,
        "transaction_budget": transaction_budget,
        "case_budget": case_budget,
        "folds": [
            benchmark_fold(
                ledger, history, accounts, dates, transaction_budget, case_budget, final=index == 1
            )
            for index, dates in enumerate(FOLDS)
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Checksum-verified HI-Small_Trans.csv")
    parser.add_argument("--output", required=True, type=Path, help="Aggregate benchmark JSON")
    parser.add_argument("--transaction-budget", type=int, default=432)
    parser.add_argument("--case-budget", type=int, default=100)
    args = parser.parse_args()
    if min(args.transaction_budget, args.case_budget) < 1:
        parser.error("Review budgets must be positive")
    args.output.write_text(
        json.dumps(benchmark(args.input, args.transaction_budget, args.case_budget), indent=2) + "\n"
    )
