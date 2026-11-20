"""Exercise user-visible evidence inspection without allowing arbitrary queries."""

import math
from copy import deepcopy
from fastapi.testclient import TestClient
import pytest

from api.core import CASES
from api.index import app
from ml.inference import predict_example, examples


@pytest.mark.parametrize("case", [case["id"] for case in CASES])
def test_graph_projection_provenance_and_validation_isolation(case):
    client = TestClient(app)
    original = deepcopy(CASES)
    valid = client.get(f"/api/explorer/{case}").json()
    assert valid["validation"]["conforms"]
    tx = valid["source"]["transactions"][0]
    assert any(
        t["subject"] == "ex:" + tx["id"]
        and t["predicate"] == "fg:sourceId"
        and t["object"] == tx["source_id"]
        for t in valid["triples"]
    )
    assert len(valid["query"]["rows"]) == len(valid["source"]["transactions"])
    for variant, expected in [
        ("negative_amount", "amount"),
        ("missing_sender", "sender"),
        ("missing_evidence", "supportedBy"),
    ]:
        invalid = client.get(f"/api/explorer/{case}", params={"variant": variant}).json()
        assert not invalid["validation"]["conforms"]
        assert not invalid["query"]["allowed_for_investigation"]
        issues = invalid["validation"]["issues"]
        assert len(issues) == 1
        assert issues[0]["path"].endswith(expected)
        if variant == "negative_amount":
            assert issues[0]["message"].startswith("Value is not >")
        assert invalid["source"] == valid["source"]
    assert CASES == original
    assert client.get(f"/api/explorer/{case}").json()["validation"]["conforms"]


def test_stable_query_and_function_entry_routes_run_the_same_live_validation():
    client = TestClient(app)
    params = {"variant": "missing_evidence", "query": "ownership"}
    legacy = client.get("/api/explorer/FG-001", params=params)
    named = client.get("/api/explorer", params={**params, "case_id": "FG-001"})
    entry = client.get("/api/index.py", params={**params, "case_id": "FG-001", "action": "rdf-explorer"})
    assert legacy.status_code == named.status_code == entry.status_code == 200
    assert legacy.json() == named.json() == entry.json()
    assert not entry.json()["validation"]["conforms"]
    assert client.get("/api/index.py?action=unregistered&case_id=FG-001").status_code == 422
    assert client.get("/api/index.py?action=rdf-explorer&case_id=absent").status_code == 404


def test_query_boundary_and_real_ownership_results():
    client = TestClient(app)
    assert client.get("/api/explorer/FG-001?query=DELETE").status_code == 422
    assert client.get("/api/explorer/FG-001?variant=arbitrary").status_code == 422
    assert client.get("/api/explorer/absent").status_code == 404
    result = client.get("/api/explorer/FG-001?query=ownership").json()
    assert all(row["source"].startswith("synthetic-registry-v1:") for row in result["query"]["rows"])
    schema = client.get("/api/ontology").json()
    assert "fg:OwnershipAssertion" in schema["classes"]


def test_explanations_reconstruct_linear_prediction_and_metrics_conserve_counts():
    for item in examples():
        result = predict_example(item["id"])
        linear = result["explanations"]["logistic"]
        logit = linear["intercept"] + sum(row["logit_contribution"] for row in linear["contributions"])
        assert 1 / (1 + math.exp(-logit)) == pytest.approx(
            result["scores"]["logistic_regression"], abs=1e-10
        )
        for row in result["explanations"]["gradient_boosting"]["sensitivity"]:
            assert row["score_difference"] == pytest.approx(
                row["original_score"] - row["reference_replacement_score"]
            )
    report = TestClient(app).get("/api/model/evaluation").json()
    for model in report["models"].values():
        for part in ["validation", "test"]:
            data = model[part]
            c = data["confusion"]
            assert c["tp"] + c["fn"] == data["positives"]
            assert sum(c[key] for key in ["tp", "fp", "tn", "fn"]) == data["rows"]
            for budget in data["review_budgets"]:
                assert budget["true_positives"] + budget["false_positives"] == budget["reviewed"]
                assert budget["true_positives"] + budget["missed"] == data["positives"]


def test_hosted_manifest_declares_the_runtime_dependencies():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    runtime = set((root / "requirements.txt").read_text().splitlines())
    assert set(project["project"]["dependencies"]) == runtime


def test_exported_plot_geometry_reproduces_metrics_for_every_model_and_split():
    report = TestClient(app).get("/api/model/evaluation").json()
    for model in report["models"].values():
        for part in ["validation", "test"]:
            result = model[part]
            pr = result["pr_curve"]
            roc = result["roc_curve"]
            assert pr[0][0] == 0 and pr[-1][0] == 1
            assert all(a[0] <= b[0] for a, b in zip(pr, pr[1:]))
            step_area = sum((b[0] - a[0]) * b[1] for a, b in zip(pr, pr[1:]))
            roc_area = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(roc, roc[1:]))
            assert step_area == pytest.approx(result["average_precision"], abs=1e-12)
            assert roc_area == pytest.approx(result["roc_auc"], abs=1e-12)
