"""Compare sampled and full pre-cutoff history on the same AMLworld target rows."""

import argparse
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from ml.prepare import EXPECTED_SHA256
from ml.train import CUTOFFS, edge_features, gradient_boosting_baseline, snapshot


def verify_file(path, expected_sha256):
    with path.open("rb") as handle:
        if file_digest(handle, "sha256").hexdigest() != expected_sha256:
            raise ValueError(f"Checksum mismatch for {path}")


def full_history(source, entities, cutoffs):
    counts = np.zeros((len(cutoffs), 2, len(entities)), dtype=np.int64)
    rows = np.zeros(len(cutoffs), dtype=np.int64)
    columns = ["Timestamp", "From Bank", "Account", "To Bank", "Account.1"]
    limits = [cutoff.strftime("%Y/%m/%d %H:%M") for cutoff in cutoffs]
    for chunk in pd.read_csv(source, usecols=columns, dtype=str, chunksize=250_000):
        senders = entities.get_indexer(chunk["From Bank"] + ":" + chunk["Account"])
        receivers = entities.get_indexer(chunk["To Bank"] + ":" + chunk["Account.1"])
        timestamps = chunk.Timestamp.to_numpy()
        for index, limit in enumerate(limits):
            earlier = timestamps < limit
            rows[index] += earlier.sum()
            for direction, indices in enumerate((receivers, senders)):
                observed = indices[earlier & (indices >= 0)]
                counts[index, direction] += np.bincount(observed, minlength=len(entities))
    features = [
        np.column_stack([np.log1p(incoming), np.log1p(outgoing), incoming > 0, outgoing > 0]).astype(
            np.float32
        )
        for incoming, outgoing in counts
    ]
    return features, rows.tolist()


def evaluate(features, degrees, partitions):
    design = [
        np.column_stack([edge, nodes[part.src], nodes[part.dst]])
        for edge, nodes, part in zip(features, degrees, partitions, strict=True)
    ]
    train, _, test = partitions
    model = gradient_boosting_baseline().fit(design[0], train.label.to_numpy())
    scores = model.predict_proba(design[2])[:, 1]
    labels = test.label.to_numpy()
    positives = int(labels.sum())
    budget = int(np.ceil(len(scores) * 0.01))
    hits = int(labels[np.argsort(-scores, kind="stable")[:budget]].sum())
    seen_sender = (degrees[2][test.src, 2:] > 0).any(axis=1)
    seen_receiver = (degrees[2][test.dst, 2:] > 0).any(axis=1)
    return {
        "test_ap": average_precision_score(labels, scores),
        "test_roc_auc": roc_auc_score(labels, scores),
        "review_budget": budget,
        "positives_found": hits,
        "labelled_negatives_reviewed": budget - hits,
        "labelled_positives_missed": positives - hits,
        "precision_at_budget": hits / budget,
        "recall_at_budget": hits / positives,
        "test_rows_with_both_accounts_in_history": int((seen_sender & seen_receiver).sum()),
        "test_rows_with_neither_account_in_history": int((~seen_sender & ~seen_receiver).sum()),
    }


def audit(source, data_dir):
    sample = data_dir / "sample.csv"
    manifest = json.loads((data_dir / "data_manifest.json").read_text())
    verify_file(source, EXPECTED_SHA256)
    verify_file(sample, manifest["sample_sha256"])
    id_columns = ["source", "target", "From Bank", "To Bank", "Account", "Account.1"]
    frame = pd.read_csv(sample, dtype=dict.fromkeys(id_columns, str))
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
    ids, entities = pd.factorize(pd.concat([frame.source, frame.target], ignore_index=True), sort=True)
    frame["src"], frame["dst"] = ids[: len(frame)], ids[len(frame) :]
    cutoffs = list(map(pd.Timestamp, CUTOFFS))
    partitions = [
        frame[(frame.timestamp >= cutoffs[0]) & (frame.timestamp < cutoffs[1])],
        frame[(frame.timestamp >= cutoffs[1]) & (frame.timestamp < cutoffs[2])],
        frame[frame.timestamp >= cutoffs[2]],
    ]
    if any(part.label.nunique() != 2 for part in partitions):
        raise ValueError("Each time partition requires both classes")
    categories = {
        column: sorted(partitions[0][column].unique())
        for column in ("Payment Currency", "Payment Format")
    }
    transaction_features = [edge_features(part, categories)[0] for part in partitions]
    scaler = StandardScaler().fit(transaction_features[0])
    features = [scaler.transform(each).astype(np.float32) for each in transaction_features]
    sampled_degrees = [snapshot(frame, cutoff, len(entities))[0].numpy() for cutoff in cutoffs]
    full_degrees, full_rows = full_history(source, entities, cutoffs)
    return {
        "dataset_sha256": EXPECTED_SHA256,
        "target_sample_sha256": manifest["sample_sha256"],
        "cutoffs": CUTOFFS,
        "target_rows": [len(part) for part in partitions],
        "target_positives": [int(part.label.sum()) for part in partitions],
        "sampled_history_edge_counts": [int((frame.timestamp < cutoff).sum()) for cutoff in cutoffs],
        "full_history_edge_counts": full_rows,
        "sampled_history": evaluate(features, sampled_degrees, partitions),
        "full_history": evaluate(features, full_degrees, partitions),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Verified HI-Small_Trans.csv")
    parser.add_argument("--data", required=True, type=Path, help="Directory created by ml.prepare")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON file")
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit(args.input, args.data), indent=2) + "\n")
