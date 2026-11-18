from fastapi.testclient import TestClient
import pytest

from api.index import app
from ml.inference import baseline_score, examples, score


def test_exported_models_match_training_runtime():
    for example in examples():
        assert score(example["head_input"]) == pytest.approx(example["reference_score"], abs=1e-6)
        for name, reference in example["baseline_reference_scores"].items():
            assert baseline_score(name, example["baseline_input"]) == pytest.approx(reference, abs=1e-6)


def test_model_normal_entry_point_and_invalid_requests():
    client = TestClient(app)
    listed = client.get("/api/model/examples")
    assert listed.status_code == 200
    assert "head_input" not in listed.json()[0]
    identifier = listed.json()[0]["id"]
    result = client.post("/api/model/score", json={"example_id": identifier})
    assert result.status_code == 200, result.text
    assert all(0 <= value <= 1 for value in result.json()["scores"].values())
    assert client.post("/api/model/score", json={"example_id": "absent"}).status_code == 404
    assert client.get("/api/model/evaluation").json()["dataset"]["source_rows"] == 5078345
    with pytest.raises(ValueError):
        score([float("nan")])
