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


def head_activations(vector):
    weights = model()["weights"]
    if len(vector) != len(weights["head.0.weight"][0]) or not all(
        math.isfinite(value) for value in vector
    ):
        raise ValueError("Invalid model feature vector")
    return [
        bias + sum(value * weight for value, weight in zip(vector, row))
        for row, bias in zip(weights["head.0.weight"], weights["head.0.bias"])
    ]


def head_logit(activations):
    weights = model()["weights"]
    return weights["head.2.bias"][0] + sum(
        max(0, value) * weight for value, weight in zip(activations, weights["head.2.weight"][0])
    )


def score(vector):
    logit = head_logit(head_activations(vector))
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
        "explanations": {
            **explain(example["baseline_input"]),
            "graphsage": explain_graphsage(example["head_input"]),
        },
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


def explain(vector):
    linear = baselines()["logistic_regression"]
    contributions = [
        {"feature": name, "value": value, "weight": weight, "logit_contribution": value * weight}
        for name, value, weight in zip(linear["feature_names"], vector, linear["weights"])
    ]
    logit = linear["intercept"] + sum(item["logit_contribution"] for item in contributions)
    tree = baselines()["histogram_gradient_boosting"]
    score_value = baseline_score("histogram_gradient_boosting", vector)
    changes = []
    for group, indices in tree["feature_groups"].items():
        replaced = list(vector)
        for index in indices:
            replaced[index] = tree["reference_vector"][index]
        alternative = baseline_score("histogram_gradient_boosting", replaced)
        changes.append(
            {
                "group": group,
                "original_score": score_value,
                "reference_replacement_score": alternative,
                "score_difference": score_value - alternative,
            }
        )
    return {
        "logistic": {
            "intercept": linear["intercept"],
            "logit": logit,
            "contributions": sorted(
                contributions, key=lambda item: abs(item["logit_contribution"]), reverse=True
            ),
            "method": "Exact additive log-odds decomposition: intercept + sum(value × weight). These are model terms, not causal effects.",
        },
        "gradient_boosting": {
            "sensitivity": sorted(changes, key=lambda item: abs(item["score_difference"]), reverse=True),
            "method": "Replace one feature group with values from the first training row and rescore the frozen model. This artificial input may be unrealistic; effects are not additive, causal, or SHAP values.",
        },
    }


def relu_active_fraction(start, end):
    """Length of the interval on which the interpolated preactivation is positive."""
    if start <= 0 and end <= 0:
        return 0.0
    if start >= 0 and end >= 0:
        return 1.0
    return max(start, end) / (abs(start) + abs(end))


def explain_graphsage(vector):
    """Exact integrated gradients of the fixed ReLU edge head, using a zero reference."""
    artifact = model()
    weights = artifact["weights"]
    start = weights["head.0.bias"]
    end = head_activations(vector)
    hidden_width = len(weights["second.bias"])
    feature_names = artifact["feature_names"]
    if 2 * hidden_width + len(feature_names) != len(vector):
        raise ValueError("GraphSAGE explanation layout does not match the edge head")

    # Along 0 → x, each hidden preactivation is linear in the path coordinate.
    # Its gradient is active over a known fraction, even when the path crosses zero.
    output_factors = [
        weight * relu_active_fraction(a, b)
        for weight, a, b in zip(weights["head.2.weight"][0], start, end, strict=True)
    ]
    terms = [
        value * math.fsum(weight * factor for weight, factor in zip(column, output_factors, strict=True))
        for value, column in zip(vector, zip(*weights["head.0.weight"], strict=True), strict=True)
    ]
    reference_logit = head_logit(start)
    logit = head_logit(end)
    groups = [
        {"group": "Sender embedding", "logit_contribution": math.fsum(terms[:hidden_width])},
        {
            "group": "Receiver embedding",
            "logit_contribution": math.fsum(terms[hidden_width : 2 * hidden_width]),
        },
        {"group": "Transaction features", "logit_contribution": math.fsum(terms[2 * hidden_width :])},
    ]
    return {
        "reference_logit": reference_logit,
        "logit": logit,
        "reconstruction_error": logit - reference_logit - math.fsum(terms),
        "groups": groups,
        "transaction_terms": sorted(
            (
                {"feature": name, "logit_contribution": term}
                for name, term in zip(feature_names, terms[2 * hidden_width :], strict=True)
            ),
            key=lambda item: abs(item["logit_contribution"]),
            reverse=True,
        ),
        "method": "Integrated gradients of the edge scorer against an all-zero input. The groups add to the logit change. Embeddings are frozen summaries of graph history, so this method does not attribute individual historical edges. The zero reference is a mathematical baseline, not a transaction.",
    }
