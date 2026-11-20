"""Metrics for the whole holdout and validation-only model diagnostics."""

import numpy as np
from sklearn.metrics import average_precision_score, confusion_matrix, precision_recall_curve, roc_curve


def evaluation_detail(labels, scores, threshold):
    precision, recall, _ = precision_recall_curve(labels, scores)
    fpr, tpr, _ = roc_curve(labels, scores)

    # Retain both ends of every recall plateau. Uniform threshold subsampling can
    # erase the high-score tail and give a misleading PR plot on rare positives.
    # Precision is monotone within each constant-recall run, so only its two
    # endpoints are needed to preserve the geometry and step-integrated AP.
    pr_x, pr_y = recall[::-1], precision[::-1]
    changes = np.flatnonzero(np.diff(pr_x) != 0)
    keep = np.unique(np.concatenate(([0, len(pr_x) - 1], changes, changes + 1)))
    roc_changes = np.flatnonzero(np.diff(tpr) != 0)
    roc_keep = np.unique(np.concatenate(([0, len(fpr) - 1], roc_changes, roc_changes + 1)))

    tn, fp, fn, tp = confusion_matrix(labels, scores >= threshold, labels=[0, 1]).ravel()
    order = np.argsort(-scores, kind="stable")
    budgets = []
    for percent in [0.1, 0.5, 1, 2, 5, 10]:
        count = int(np.ceil(len(labels) * percent / 100))
        found = int(labels[order[:count]].sum())
        budgets.append(
            {
                "percent": percent,
                "reviewed": count,
                "true_positives": found,
                "false_positives": count - found,
                "missed": int(labels.sum()) - found,
                "precision": found / count,
                "recall": found / int(labels.sum()),
            }
        )
    bins = []
    for start in np.arange(0, 1, 0.1):
        mask = (scores >= start) & (scores < start + 0.1 if start < 0.89 else scores <= 1)
        if mask.any():
            bins.append(
                {
                    "lower": float(start),
                    "upper": float(start + 0.1),
                    "count": int(mask.sum()),
                    "mean_score": float(scores[mask].mean()),
                    "positive_rate": float(labels[mask].mean()),
                }
            )
    return {
        "pr_curve": [[float(pr_x[i]), float(pr_y[i])] for i in keep],
        "roc_curve": [[float(fpr[i]), float(tpr[i])] for i in roc_keep],
        "curve_policy": (
            "PR retains every recall change and plateau endpoint; render as steps. "
            "ROC retains every true-positive-rate change and plateau endpoint. No uniform subsampling; "
            "both plotted areas reproduce their headline metrics."
        ),
        "confusion": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
            "threshold": threshold,
            "accuracy": float((tp + tn) / len(labels)),
            "all_negative_accuracy": float(1 - labels.mean()),
        },
        "review_budgets": budgets,
        "calibration_bins": bins,
    }


def feature_groups(names):
    groups = {}
    for index, name in enumerate(names):
        key = name.split("=", 1)[0]
        if key.startswith("sender_"):
            key = "Sender historical activity"
        elif key.startswith("receiver_"):
            key = "Receiver historical activity"
        groups.setdefault(key, []).append(index)
    return groups


def permutation_diagnostics(estimator, features, labels, groups):
    baseline = average_precision_score(labels, estimator.predict_proba(features)[:, 1])
    rng = np.random.default_rng(2026)
    records = []
    for name, columns in groups.items():
        drops = []
        for _ in range(3):
            changed = features.copy()
            changed[:, columns] = features[rng.permutation(len(features))][:, columns]
            ap = average_precision_score(labels, estimator.predict_proba(changed)[:, 1])
            drops.append(float(baseline - ap))
        records.append(
            {
                "group": name,
                "mean_ap_drop": float(np.mean(drops)),
                "repeat_drops": drops,
                "std": float(np.std(drops)),
            }
        )
    return {
        "partition": "validation",
        "repeats": 3,
        "baseline_ap": float(baseline),
        "method": "Jointly shuffle each feature group across validation rows; hold all other features fixed.",
        "interpretation": "AP drop measures model reliance under this perturbation, not causality. Correlated groups can share signal; 32 validation positives make estimates unstable.",
        "groups": sorted(records, key=lambda row: row["mean_ap_drop"], reverse=True),
    }
