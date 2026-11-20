"""Directed GraphSAGE edge classification against two tabular baselines.

Each evaluation period uses graph history strictly before that period. Target
edges and their labels never enter that period's graph. Equal-time edges remain
in the same partition. Sampling and model limitations are recorded in metrics.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler
import torch
from torch import nn
from ml.diagnostics import evaluation_detail, feature_groups, permutation_diagnostics

CUTOFFS = ["2022-09-04T00:00:00Z", "2022-09-08T00:00:00Z", "2022-09-09T00:00:00Z"]


class DirectedSAGE(nn.Module):
    def __init__(self, edge_features, hidden=16):
        super().__init__()
        self.first = nn.Linear(12, hidden)
        self.second = nn.Linear(hidden * 3, hidden)
        self.head = nn.Sequential(nn.Linear(hidden * 2 + edge_features, 32), nn.ReLU(), nn.Linear(32, 1))

    @staticmethod
    def aggregate(features, source, target):
        total = torch.zeros_like(features)
        total.index_add_(0, target, features[source])
        degree = torch.zeros(len(features), device=features.device)
        degree.index_add_(0, target, torch.ones_like(target, dtype=features.dtype))
        return total / degree.clamp_min(1).unsqueeze(1)

    def encode(self, features, edges):
        source, target = edges
        incoming = self.aggregate(features, source, target)
        outgoing = self.aggregate(features, target, source)
        first = torch.relu(self.first(torch.cat([features, incoming, outgoing], dim=1)))
        incoming = self.aggregate(first, source, target)
        outgoing = self.aggregate(first, target, source)
        return torch.relu(self.second(torch.cat([first, incoming, outgoing], dim=1)))

    def forward(self, nodes, history, source, target, edges):
        embeddings = self.encode(nodes, history)
        return self.head(torch.cat([embeddings[source], embeddings[target], edges], dim=1)).squeeze(1)


def edge_features(frame, categories):
    paid = frame.paid.to_numpy(dtype=float)
    received = frame.received.to_numpy(dtype=float)
    same_currency = (frame["Payment Currency"] == frame["Receiving Currency"]).to_numpy()
    columns = [
        np.log1p(paid),
        (frame.source == frame.target).to_numpy(),
        (frame["From Bank"] == frame["To Bank"]).to_numpy(),
        (~same_currency).astype(float),
        np.where(same_currency, np.log1p(received) - np.log1p(paid), 0),
    ]
    names = [
        "log1p_paid",
        "self_transfer",
        "same_bank",
        "currency_change",
        "same_currency_log_amount_difference",
    ]
    for field, values in categories.items():
        for value in values:
            columns.append((frame[field] == value).to_numpy())
            names.append(field + "=" + value)
        columns.append((~frame[field].isin(values)).to_numpy())
        names.append(field + "=UNKNOWN")
    return np.asarray(columns, dtype=np.float32).T, names


def snapshot(frame, cutoff, nodes):
    history = frame[frame.timestamp < cutoff]
    source = torch.tensor(history.src.to_numpy(), dtype=torch.long)
    target = torch.tensor(history.dst.to_numpy(), dtype=torch.long)
    incoming = np.bincount(history.dst, minlength=nodes)
    outgoing = np.bincount(history.src, minlength=nodes)
    features = np.column_stack([np.log1p(incoming), np.log1p(outgoing), incoming > 0, outgoing > 0])
    return torch.tensor(features, dtype=torch.float32), (source, target)


def metrics(labels, scores, threshold):
    if len(np.unique(labels)) != 2:
        raise ValueError("Both classes are required for held-out evaluation")
    count = max(1, int(np.ceil(len(labels) * 0.01)))
    top = np.argsort(-scores, kind="stable")[:count]
    return {
        **evaluation_detail(labels, scores, threshold),
        "rows": len(labels),
        "positives": int(labels.sum()),
        "prevalence": float(labels.mean()),
        "average_precision": float(average_precision_score(labels, scores)),
        "roc_auc": float(roc_auc_score(labels, scores)),
        "brier": float(brier_score_loss(labels, scores)),
        "threshold": float(threshold),
        "precision": float(precision_score(labels, scores >= threshold, zero_division=0)),
        "recall": float(recall_score(labels, scores >= threshold, zero_division=0)),
        "alert_budget": count,
        "precision_at_1_percent": float(labels[top].mean()),
        "recall_at_1_percent": float(labels[top].sum() / labels.sum()),
    }


def choose_threshold(labels, scores):
    precision, recall, thresholds = precision_recall_curve(labels, scores)
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    return float(thresholds[np.argmax(f1)])


def gradient_boosting_baseline():
    return HistGradientBoostingClassifier(
        max_iter=100,
        max_leaf_nodes=15,
        learning_rate=0.08,
        l2_regularization=1,
        class_weight="balanced",
        random_state=42,
        early_stopping=False,
    )


def train(data_dir, output, epochs=20):
    started = time.monotonic()
    torch.manual_seed(42)
    np.random.seed(42)
    torch.set_num_threads(4)
    frame = pd.read_csv(
        data_dir / "sample.csv",
        dtype={
            "source": str,
            "target": str,
            "From Bank": str,
            "To Bank": str,
            "Account": str,
            "Account.1": str,
        },
    )
    frame.timestamp = pd.to_datetime(frame.timestamp, utc=True)
    ids, entities = pd.factorize(pd.concat([frame.source, frame.target], ignore_index=True), sort=True)
    frame["src"], frame["dst"] = ids[: len(frame)], ids[len(frame) :]
    cutoffs = [pd.Timestamp(value) for value in CUTOFFS]
    periods = [
        frame[(frame.timestamp >= cutoffs[0]) & (frame.timestamp < cutoffs[1])],
        frame[(frame.timestamp >= cutoffs[1]) & (frame.timestamp < cutoffs[2])],
        frame[frame.timestamp >= cutoffs[2]],
    ]
    for period in periods:
        if period.label.nunique() != 2:
            raise ValueError("Each time split must contain both classes")
    categories = {
        field: sorted(periods[0][field].unique().tolist())
        for field in ["Payment Currency", "Payment Format"]
    }
    features = [edge_features(part, categories)[0] for part in periods]
    feature_names = edge_features(periods[0], categories)[1]
    scaler = StandardScaler().fit(features[0])
    features = [scaler.transform(values).astype(np.float32) for values in features]
    snapshots = [snapshot(frame, cutoff, len(entities)) for cutoff in cutoffs]
    tensors = [
        (
            torch.tensor(part.src.to_numpy(), dtype=torch.long),
            torch.tensor(part.dst.to_numpy(), dtype=torch.long),
            torch.tensor(values),
            torch.tensor(part.label.to_numpy(), dtype=torch.float32),
        )
        for part, values in zip(periods, features)
    ]
    model = DirectedSAGE(features[0].shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.003, weight_decay=1e-4)
    labels = [part.label.to_numpy() for part in periods]
    weight = np.sqrt((1 - labels[0].mean()) / labels[0].mean())
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight, dtype=torch.float32))
    best_ap, best_state, history = -1, None, []
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        source, target, edges, target_labels = tensors[0]
        logits = model(*snapshots[0], source, target, edges)
        loss = criterion(logits, target_labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
        model.eval()
        with torch.no_grad():
            source, target, edges, _ = tensors[1]
            validation = torch.sigmoid(model(*snapshots[1], source, target, edges)).numpy()
        ap = average_precision_score(labels[1], validation)
        history.append({"epoch": epoch + 1, "loss": float(loss.detach()), "validation_ap": float(ap)})
        print(json.dumps(history[-1]), flush=True)
        if ap > best_ap:
            best_ap = ap
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    scores = []
    with torch.no_grad():
        for snap, values in zip(snapshots, tensors):
            source, target, edges, _ = values
            scores.append(torch.sigmoid(model(*snap, source, target, edges)).numpy())
        test_embeddings = model.encode(*snapshots[2]).numpy()
    threshold = choose_threshold(labels[1], scores[1])
    results = {
        "directed_graphsage": {
            "validation": metrics(labels[1], scores[1], threshold),
            "test": metrics(labels[2], scores[2], threshold),
        }
    }
    # Same causal node-degree features and transaction fields are available to both baselines.
    tabular = [
        np.column_stack([values, snap[0].numpy()[part.src], snap[0].numpy()[part.dst]])
        for values, snap, part in zip(features, snapshots, periods)
    ]
    baseline_exports = {}
    baseline_scores = {}
    baseline_names = feature_names + [
        f"{side}_{field}"
        for side in ["sender", "receiver"]
        for field in ["log_in_count", "log_out_count", "has_in_history", "has_out_history"]
    ]
    groups = feature_groups(baseline_names)
    diagnostics = {}
    for name, estimator in [
        (
            "logistic_regression",
            LogisticRegression(max_iter=300, class_weight="balanced", random_state=42),
        ),
        ("histogram_gradient_boosting", gradient_boosting_baseline()),
    ]:
        estimator.fit(tabular[0], labels[0])
        validation = estimator.predict_proba(tabular[1])[:, 1]
        test = estimator.predict_proba(tabular[2])[:, 1]
        baseline_scores[name] = test
        diagnostics[name] = permutation_diagnostics(estimator, tabular[1], labels[1], groups)
        if name == "logistic_regression":
            baseline_exports[name] = {
                "weights": estimator.coef_[0].tolist(),
                "intercept": float(estimator.intercept_[0]),
            }
        else:
            baseline_exports[name] = {
                "baseline": float(estimator._baseline_prediction[0, 0]),
                "trees": [
                    [
                        {
                            key: (
                                int(node[key])
                                if key in {"feature_idx", "left", "right", "is_leaf"}
                                else float(node[key])
                            )
                            for key in [
                                "feature_idx",
                                "num_threshold",
                                "left",
                                "right",
                                "is_leaf",
                                "value",
                            ]
                        }
                        for node in predictors[0].nodes
                    ]
                    for predictors in estimator._predictors
                ],
            }
        decision_threshold = choose_threshold(labels[1], validation)
        results[name] = {
            "validation": metrics(labels[1], validation, decision_threshold),
            "test": metrics(labels[2], test, decision_threshold),
        }
        baseline_exports[name]["feature_names"] = baseline_names
        baseline_exports[name]["feature_groups"] = groups
        # One real training feature row is used as a reproducible local perturbation reference.
        baseline_exports[name]["reference_vector"] = tabular[0][0].tolist()
    output.mkdir(parents=True, exist_ok=True)
    (output / "baseline_models.json").write_text(json.dumps(baseline_exports, separators=(",", ":")))
    weights = {key: value.numpy().tolist() for key, value in best_state.items()}
    artifact = {
        "model_id": "directed-graphsage-amlworld-v1",
        "weights": weights,
        "feature_names": feature_names,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "categories": categories,
        "validation_threshold": threshold,
        "graph_cutoff": CUTOFFS[2],
        "score_semantics": "Uncalibrated experimental model score",
    }
    (output / "gnn_model.json").write_text(json.dumps(artifact, separators=(",", ":")))
    manifest = json.loads((data_dir / "data_manifest.json").read_text())
    report = {
        "dataset": manifest,
        "model_id": artifact["model_id"],
        "seed": 42,
        "epochs": epochs,
        "selected_epoch": int(np.argmax([item["validation_ap"] for item in history])) + 1,
        "graph_cutoffs": CUTOFFS,
        "split_policy": "warmup before Sep 4; train Sep 4-7; validation Sep 8; test Sep 9 onward",
        "graph_policy": "Frozen past-only directed graph per partition; target-period edges excluded; node IDs are indices only",
        "nodes": len(entities),
        "train_rows": len(periods[0]),
        "train_positives": int(labels[0].sum()),
        "models": results,
        "validation_permutation": diagnostics,
        "training_history": history,
        "training_seconds": round(time.monotonic() - started, 2),
        "model_sha256": sha256((output / "gnn_model.json").read_bytes()).hexdigest(),
        "runtime": {"torch": torch.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "limitations": [
            "5% label-blind transaction sample removes graph neighbors and motifs",
            "Synthetic single-source benchmark; one seed and one temporal holdout",
            "No calibration fit; model scores support ranking experiments",
            "Sparse late-period activity can create temporal distribution shift",
            "Explicit source timezones and delayed-arrival fields are unavailable",
            "External bank performance and analyst effort remain unmeasured",
        ],
    }
    (output / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    # Curated inference cards are distinct from the full held-out metric calculation.
    chosen = list(np.argsort(-scores[2])[:4])
    chosen += list(np.flatnonzero(labels[2])[:4])
    chosen += list(np.linspace(0, len(labels[2]) - 1, 8, dtype=int))
    examples = []
    for index in dict.fromkeys(chosen):
        row = periods[2].iloc[index]
        vector = np.concatenate(
            [test_embeddings[int(row.src)], test_embeddings[int(row.dst)], features[2][index]]
        )
        examples.append(
            {
                "id": f"IBM-{int(row.source_row)}",
                "source_row": int(row.source_row),
                "sender": row.source,
                "receiver": row.target,
                "timestamp": row.timestamp.isoformat(),
                "amount": str(row.paid),
                "currency": row["Payment Currency"],
                "format": row["Payment Format"],
                "known_synthetic_label": int(row.label),
                "head_input": vector.tolist(),
                "baseline_input": tabular[2][index].tolist(),
                "baseline_reference_scores": {
                    name: float(values[index]) for name, values in baseline_scores.items()
                },
                "reference_score": float(scores[2][index]),
                "graph_cutoff": CUTOFFS[2],
            }
        )
    (output / "inference_examples.json").write_text(json.dumps(examples, indent=2) + "\n")
    print(json.dumps({"finished": True, "models": results, "seconds": report["training_seconds"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    parser.add_argument("--epochs", type=int, default=20)
    args = parser.parse_args()
    train(args.data, args.output, args.epochs)
