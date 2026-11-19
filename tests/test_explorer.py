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
        assert any(i["path"].endswith(expected) for i in invalid["validation"]["issues"])
        assert invalid["source"] == valid["source"]
    assert CASES == original
    assert client.get(f"/api/explorer/{case}").json()["validation"]["conforms"]


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
