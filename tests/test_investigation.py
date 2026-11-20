from copy import deepcopy
from decimal import Decimal
from fastapi.testclient import TestClient
from rdflib import Literal, XSD
from langgraph.types import Command
from api.index import app
from api.core import CASES, FG, EX, graph_for, validate_graph, investigate, risk_signals, build_workflow

client = TestClient(app)


def test_api_contract():
    assert client.get("/api/health").json()["models_trained"] is True
    assert len(client.get("/api/cases").json()) == 5
    assert client.post("/api/investigate", json={"case_id": "missing"}).status_code == 404
    assert (
        client.post("/api/investigate", json={"case_id": "FG-001", "query": "DROP ALL"}).status_code
        == 422
    )


def test_fanout_grounded_and_paused():
    result = client.post("/api/investigate", json={"case_id": "FG-001"}).json()
    assert result["status"] == "awaiting_analyst_review"
    assert result["draft"]["heuristic_score"] == 40
    assert result["signals"][0]["evidence"] == ["T001", "T002", "T003", "T004"]
    assert len(result["trace"]) == 5
    assert result["validation"]["conforms"]
    assert result["provenance"]["trained_model"] is None


def test_cycle_requires_chronology():
    assert risk_signals(CASES[1])[0]["type"] == "circular_flow"
    changed = deepcopy(CASES[1])
    changed["transactions"][2]["timestamp"] = "2026-11-01T10:10:00Z"
    assert risk_signals(changed) == []


def test_cycle_does_not_cross_currency():
    changed = deepcopy(CASES[1])
    changed["transactions"][2]["currency"] = "EUR"
    assert risk_signals(changed) == []


def test_missing_ownership_is_gap_not_innocence():
    r = investigate(CASES[3])
    assert r["ownership"]["missing"] == ["A001"]
    assert any("Missing" in s for s in r["draft"]["gaps"])


def test_shared_holders_preserve_each_registry_source_and_count_distinct_accounts():
    case = deepcopy(CASES[0])
    first, second, third, *_ = case["accounts"]
    second["holder"] = first["holder"]
    inactive = case["accounts"][-1]
    inactive.update(holder=third["holder"], owner=third["owner"])

    result = investigate(case)
    assert result["status"] == "awaiting_analyst_review"
    assert result["ownership"]["shared"] == [{"owner": first["owner"], "accounts": ["A001", "A002"]}]
    registry = [entry for entry in result["evidence"] if entry["kind"] == "ownership"]
    assert len(registry) == len(case["accounts"])
    assert len({entry["id"] for entry in registry}) == len(registry)
    sources = {f"registry-{account['id']}": account["ownership_source"] for account in case["accounts"]}
    assert {entry["id"]: entry["source"] for entry in registry} == sources
    joined_sources = {
        (row["account"], row["evidence"]): row["source"] for row in result["ownership"]["assertions"]
    }
    assert joined_sources[("A001", "registry-A002")] == second["ownership_source"]
    assert joined_sources[("A002", "registry-A001")] == first["ownership_source"]


def test_stale_screening_never_cleared():
    r = investigate(CASES[4])
    assert r["screening"]["status"] == "stale"
    assert "clearance" in r["screening"]["conclusion"]


def test_ordinary_is_not_automatically_cleared():
    r = investigate(CASES[2])
    assert r["draft"]["heuristic_score"] == 0
    assert r["status"] == "awaiting_analyst_review"
    assert "does not establish" in r["draft"]["findings"][0]["text"]


def test_shacl_negative_amount_and_missing_source():
    g = graph_for(CASES[0])
    g.set((EX.T001, FG.amount, Literal(Decimal("-1"), datatype=XSD.decimal)))
    g.remove((EX.T001, FG.sourceId, None))
    r = validate_graph(g)
    assert not r["conforms"]
    assert len(r["issues"]) >= 2


def test_quarantine_before_agents():
    c = deepcopy(CASES[0])
    c["transactions"][0]["amount"] = "-1"
    assert investigate(c)["status"] == "quarantined"


def test_workflow_review_resume():
    graph = build_workflow()
    config = {"configurable": {"thread_id": "test-review"}}
    r = graph.invoke({"case": CASES[0], "trace": []}, config)
    assert "__interrupt__" in r
    decision = {"action": "reject", "reviewer": "test-analyst", "reason": "More evidence required"}
    r = graph.invoke(Command(resume=decision), config)
    assert r["decision"] == decision


def test_fixture_output_deterministic():
    a = investigate(CASES[0])
    b = investigate(CASES[0])
    assert a == b
