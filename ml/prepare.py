"""Read the official IBM HI-Small CSV and take a label-blind Bernoulli sample."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

SOURCE_URL = "https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
EXPECTED_SHA256 = "b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040"
COLUMNS = [
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account.1",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
]


def prepare(source, output, fraction=0.05, seed=42):
    if not 0 < fraction <= 1:
        raise ValueError("sample fraction must be in (0, 1]")
    checksum = sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    if checksum.hexdigest() != EXPECTED_SHA256:
        raise ValueError("Dataset checksum differs from the verified HI-Small release")
    random = np.random.default_rng(seed)
    selected, seen, positives = [], 0, 0
    for chunk in pd.read_csv(source, chunksize=250000, dtype=str):
        if list(chunk.columns) != COLUMNS:
            raise ValueError("Unexpected source schema, including repeated Account columns")
        mask = random.random(len(chunk)) < fraction
        chosen = chunk.loc[mask].copy()
        chosen["source_row"] = np.flatnonzero(mask) + seen + 2
        selected.append(chosen)
        seen += len(chunk)
        positives += int(chunk["Is Laundering"].astype(int).sum())
    data = pd.concat(selected, ignore_index=True)
    data["timestamp"] = pd.to_datetime(data["Timestamp"], format="%Y/%m/%d %H:%M", utc=True)
    data = data.sort_values(["timestamp", "source_row"], kind="stable").reset_index(drop=True)
    data["source"] = data["From Bank"] + ":" + data["Account"]
    data["target"] = data["To Bank"] + ":" + data["Account.1"]
    data["label"] = data["Is Laundering"].astype(np.int8)
    data["paid"] = pd.to_numeric(data["Amount Paid"])
    data["received"] = pd.to_numeric(data["Amount Received"])
    if (
        not np.isfinite(data[["paid", "received"]].to_numpy()).all()
        or (data[["paid", "received"]] < 0).any().any()
    ):
        raise ValueError("Nonfinite or negative source amount")
    output.mkdir(parents=True, exist_ok=True)
    data.to_csv(output / "sample.csv", index=False)
    manifest = {
        "dataset": "IBM AMLworld HI-Small",
        "source_url": SOURCE_URL,
        "license": "CDLA-Sharing-1.0",
        "source_sha256": checksum.hexdigest(),
        "source_rows": seen,
        "source_positive_rows": positives,
        "sample_rows": len(data),
        "sample_positive_rows": int(data.label.sum()),
        "sample_fraction": fraction,
        "seed": seed,
        "sampling": "Bernoulli selection before reading labels; generator seed 42",
        "dataset_time_assumption": "Source timestamps treated as UTC for benchmark ordering",
        "sample_sha256": sha256((output / "sample.csv").read_bytes()).hexdigest(),
    }
    (output / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/processed"))
    parser.add_argument("--fraction", type=float, default=0.05)
    args = parser.parse_args()
    prepare(args.input, args.output, args.fraction)
