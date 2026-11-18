"""Small JSON-only edge-head runtime for precomputed causal GNN embeddings."""

import json
import math
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def model():
    return json.loads((ROOT / "artifacts/gnn_model.json").read_text())


@lru_cache
def examples():
    return json.loads((ROOT / "artifacts/inference_examples.json").read_text())


def score(vector):
    weights = model()["weights"]
    if len(vector) != len(weights["head.0.weight"][0]) or not all(
        math.isfinite(value) for value in vector
    ):
        raise ValueError("Invalid model feature vector")
    hidden = [
        max(0, bias + sum(value * weight for value, weight in zip(vector, row)))
        for row, bias in zip(weights["head.0.weight"], weights["head.0.bias"])
    ]
    logit = weights["head.2.bias"][0] + sum(
        value * weight for value, weight in zip(hidden, weights["head.2.weight"][0])
    )
    return 1 / (1 + math.exp(-max(-700, min(700, logit))))


def predict_example(identifier):
    example = next((item for item in examples() if item["id"] == identifier), None)
    if example is None:
        raise KeyError(identifier)
    value = score(example["head_input"])
    return {
        "scores": {
            "directed_graphsage": value,
            **{name: baseline_score(name, example["baseline_input"]) for name in baselines()},
        },
        "id": identifier,
        "model_id": model()["model_id"],
        "score": value,
        "threshold": model()["validation_threshold"],
        "above_threshold": value >= model()["validation_threshold"],
        "known_synthetic_label": example["known_synthetic_label"],
        "graph_cutoff": example["graph_cutoff"],
        "inference": "Live edge-head computation using graph embeddings precomputed before the test period",
        "score_semantics": model()["score_semantics"],
    }


@lru_cache
def baselines():
    return json.loads((ROOT / "artifacts/baseline_models.json").read_text())


def baseline_score(name, vector):
    artifact = baselines()[name]
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Nonfinite model input")
    if name == "logistic_regression":
        logit = artifact["intercept"] + sum(x * w for x, w in zip(vector, artifact["weights"]))
    else:
        logit = artifact["baseline"]
        for tree in artifact["trees"]:
            node = tree[0]
            while not node["is_leaf"]:
                index = (
                    node["left"]
                    if vector[node["feature_idx"]] <= node["num_threshold"]
                    else node["right"]
                )
                node = tree[index]
            logit += node["value"]
    return 1 / (1 + math.exp(-max(-700, min(700, logit))))
