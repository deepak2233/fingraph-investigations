from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from threading import Barrier

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from api.index import app
from pipeline.auth import Principal, principal
from pipeline.cli import fixture_events
from pipeline.contracts import CaseEvent, Review
from pipeline.neptune import FileProjection, NeptuneProjection, graph_name, project_pending
from pipeline.service import InvestigationService
from pipeline.store import Conflict, NotFound, Store, canonical, cases, events, outbox, quarantine
from pipeline.worker import handle_message


@pytest.fixture
def store(tmp_path):
    database = Store(f"sqlite:///{tmp_path}/cases.db")
    database.initialize()
    yield database
    database.engine.dispose()


def test_idempotency_conflict_and_restart(store):
    payload = fixture_events()[0]
    service = InvestigationService(store)
    first = service.ingest(payload)
    assert service.ingest(payload) == first
    changed = deepcopy(payload)
    changed["case"]["title"] = "Changed payload"
    with pytest.raises(Conflict):
        service.ingest(changed)
    restarted = Store(str(store.engine.url))
    review = Review(action="approve", reason="Evidence checked", expected_version=1, request_id="r1")
    saved = InvestigationService(restarted).review("demo", first["run_id"], "analyst-1", review)
    assert saved["status"] == "reviewed"
    assert saved["version"] == 2
    assert InvestigationService(restarted).review("demo", first["run_id"], "analyst-1", review) == saved
    trail = restarted.audit_trail("demo", first["run_id"])
    assert len(trail) == 2
    assert trail[1]["previous_sha256"] == trail[0]["sha256"]
    for record in trail:
        digest = record.pop("sha256")
        assert sha256(canonical(record).encode()).hexdigest() == digest
    with pytest.raises(NotFound):
        restarted.get("another-tenant", first["run_id"])
    restarted.engine.dispose()


def test_concurrent_review_has_one_winner(store):
    service = InvestigationService(store)
    run = service.ingest(fixture_events()[0])
    barrier = Barrier(2)

    def review(index):
        body = Review(action="approve", reason="Reviewed", expected_version=1, request_id=f"r{index}")
        barrier.wait()
        try:
            return service.review("demo", run["run_id"], f"analyst-{index}", body)["status"]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, [1, 2]))
    assert sorted(results) == ["conflict", "reviewed"]
    assert len(store.audit_trail("demo", run["run_id"])) == 2


def test_atomic_rollback_if_audit_write_fails(store, monkeypatch):
    def fail(*_):
        raise RuntimeError("Simulated database failure")

    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(RuntimeError):
        InvestigationService(store).ingest(fixture_events()[0])
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(events)).scalar() == 0
        assert connection.execute(select(func.count()).select_from(cases)).scalar() == 0


def test_projection_failure_and_replay(store, tmp_path):
    run = InvestigationService(store).ingest(fixture_events()[0])

    class FailingProjection:
        def write(self, *_):
            raise OSError("Simulated network loss")

    assert project_pending(store, FailingProjection()) == 0
    assert store.pending_projections()[0]["attempts"] == 1
    projection = FileProjection(tmp_path / "rdf")
    assert project_pending(store, projection) == 1
    assert project_pending(store, projection) == 0
    assert len(list((tmp_path / "rdf").glob("*.nt"))) == 1
    assert store.get("demo", run["run_id"])["status"] == "awaiting_analyst_review"
    store.projection_result(run["run_id"], "Late error from another attempt")
    with store.engine.connect() as connection:
        assert connection.execute(select(outbox.c.status)).scalar_one() == "published"


def test_attempted_projection_does_not_block_fresh_row(store):
    service = InvestigationService(store)
    failed = service.ingest(fixture_events()[0])
    store.projection_result(failed["run_id"], "Unavailable")
    fresh = service.ingest(fixture_events()[1])

    assert store.pending_projections(limit=1)[0]["id"] == fresh["run_id"]


def test_neptune_request_is_signed_and_graph_is_scoped():
    import boto3

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    session = boto3.Session(aws_access_key_id="test-key", aws_secret_access_key="test-secret")
    projection = NeptuneProjection(
        "https://neptune.example:8182",
        "us-east-1",
        session,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    projection.write("tenant-a", "run-1", fixture_events()[0]["case"])
    request = requests[0]
    assert "AWS4-HMAC-SHA256" in request.headers["Authorization"]
    assert "/neptune-db/aws4_request" in request.headers["Authorization"]
    assert graph_name("tenant-a", "run-1") in request.content.decode()
    assert graph_name("tenant-a", "run-1") != graph_name("tenant-b", "run-1")


@pytest.mark.parametrize("payload", [b'{"malformed": true}', None])
def test_invalid_records_are_quarantined_and_database_failures_propagate(store, monkeypatch, payload):
    class Message:
        def value(self):
            return payload

        def topic(self):
            return "cases"

        def partition(self):
            return 0

        def offset(self):
            return 12

    service = InvestigationService(store)
    handle_message(service, Message())
    handle_message(service, Message())
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(quarantine)).scalar() == 1

    def fail(*_):
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(store, "quarantine_record", fail)
    with pytest.raises(RuntimeError):
        handle_message(service, Message())


def test_unexpected_investigation_error_is_not_quarantined(store, monkeypatch):
    import json

    class Message:
        def value(self):
            return json.dumps(fixture_events()[0]).encode()

        def topic(self):
            return "cases"

        def partition(self):
            return 0

        def offset(self):
            return 12

    def fail(_):
        raise ValueError("Unexpected investigation error")

    service = InvestigationService(store)
    monkeypatch.setattr(service, "ingest", fail)
    with pytest.raises(ValueError, match="Unexpected investigation error"):
        handle_message(service, Message())
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(quarantine)).scalar_one() == 0


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "unknown_account", "future", "naive_time", "missing_source", "invented_ownership"],
)
def test_wire_contract_rejects_invalid_evidence(mutation):
    payload = deepcopy(fixture_events()[0])
    case = payload["case"]
    if mutation == "duplicate":
        case["transactions"].append(case["transactions"][0])
    if mutation == "unknown_account":
        case["transactions"][0]["receiver"] = "missing"
    if mutation == "future":
        case["transactions"][0]["timestamp"] = "2027-01-01T00:00:00Z"
    if mutation == "naive_time":
        case["transactions"][0]["timestamp"] = "2026-11-01T00:00:00"
    if mutation == "missing_source":
        case["transactions"][0]["source_id"] = "  "
    if mutation == "invented_ownership":
        case["accounts"][0].pop("owner_percentage")
    with pytest.raises(ValidationError):
        CaseEvent.model_validate(payload)


def test_http_private_flow_roles_tenant_and_review(store):
    app.dependency_overrides[principal] = lambda: Principal(
        "analyst-a", "demo", frozenset(["ingest", "analyst", "reviewer"])
    )
    import api.index as api

    original_store = api.store
    api.store = lambda: store
    try:
        client = TestClient(app)
        response = client.post("/api/v1/events", json=fixture_events()[0])
        assert response.status_code == 201, response.text
        result = response.json()
        assert "workflow_state" not in result
        run_id = result["run_id"]
        body = {
            "action": "request_evidence",
            "reason": "Verify ownership source",
            "expected_version": 1,
            "request_id": "r1",
        }
        decision = client.post(f"/api/v1/investigations/{run_id}/reviews", json=body)
        assert decision.status_code == 200, decision.text
        assert decision.json()["decision"]["reviewer"] == "analyst-a"
        app.dependency_overrides[principal] = lambda: Principal(
            "analyst-b", "another", frozenset(["analyst"])
        )
        assert client.get(f"/api/v1/investigations/{run_id}").status_code == 404
        app.dependency_overrides[principal] = lambda: Principal("viewer", "demo", frozenset(["analyst"]))
        assert client.post(f"/api/v1/investigations/{run_id}/reviews", json=body).status_code == 403
    finally:
        app.dependency_overrides.clear()
        api.store = original_store


def test_public_api_disallows_custom_payload_and_excessive_body():
    client = TestClient(app)
    assert client.post("/api/investigate", json={"case_id": "FG-001", "case": {}}).status_code == 422
    assert client.post("/api/investigate", content=b"x" * 2_000_001).status_code == 413
    assert client.get("/pipeline/store.py").status_code == 404
    assert client.get("/api/v1/investigations").status_code in {401, 503}


def test_candidate_builder_is_deterministic_and_uses_bounded_history():
    from pipeline.candidates import build_cases

    payload = {**fixture_events()[0]["case"], "tenant_id": "demo", "lookback_hours": 24}
    generated = build_cases(payload)
    assert len(generated) == 1
    assert generated == build_cases(payload)
    assert generated[0]["case"]["title"] == "Rapid Fan Out"
    old = deepcopy(payload)
    old["as_of"] = "2026-11-04T00:00:00Z"
    assert build_cases(old) == []


def test_oidc_signature_issuer_and_expiry(monkeypatch):
    import time
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import HTTPException
    import jwt
    import pipeline.auth as auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class JWKS:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=key.public_key())

    monkeypatch.setenv("OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("OIDC_AUDIENCE", "fingraph")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://identity.example/jwks")
    monkeypatch.setattr(auth, "jwks_client", lambda _: JWKS())
    claims = {
        "sub": "reviewer-1",
        "iss": "https://identity.example",
        "aud": "fingraph",
        "tenant_id": "demo",
        "roles": ["reviewer"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    assert auth.principal("Bearer " + token).subject == "reviewer-1"
    for changed in [
        {**claims, "exp": int(time.time()) - 60},
        {**claims, "iss": "https://other.example"},
    ]:
        with pytest.raises(HTTPException) as error:
            auth.principal("Bearer " + jwt.encode(changed, key, algorithm="RS256"))
        assert error.value.status_code == 401
