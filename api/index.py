"""Public synthetic workbench and OIDC-protected durable investigation API."""

from contextlib import asynccontextmanager
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from api.core import CASES, investigate
from api.explorer import Query, Variant, explore, ontology_schema
from ml.inference import examples, predict_example
from pipeline.auth import Principal, principal
from pipeline.contracts import CaseEvent, Review
from pipeline.service import InvalidEvidence, InvestigationService
from pipeline.store import Conflict, NotFound, Store

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.4.1"
DEMO_ASSETS = {"demo/catalog.json", "demo/scores.json", *(f"demo/{case['id']}.json" for case in CASES)}
logger = logging.getLogger("fingraph.api")


@lru_cache
def store():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(503, "Durable service requires DATABASE_URL")
    return Store(url)


@asynccontextmanager
async def lifespan(_):
    yield
    if store.cache_info().currsize:
        store().engine.dispose()
    store.cache_clear()


app = FastAPI(
    title="FinGraph Investigations",
    version=VERSION,
    lifespan=lifespan,
    description="Synthetic research workbench with a separate authenticated, durable service API.",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = str(uuid4())
    started = time.monotonic()
    # Stream counting covers clients that omit or falsify Content-Length.
    if request.method in {"POST", "PUT", "PATCH"}:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2_000_000:
                return JSONResponse({"detail": "Request exceeds 2 MB"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = (
        "no-store" if request.url.path.startswith("/api/") else "public, max-age=60"
    )
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "route": request.scope.get("route").path if request.scope.get("route") else "unmatched",
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            }
        )
    )
    return response


@app.exception_handler(Conflict)
async def conflict(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(NotFound)
async def missing(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(InvalidEvidence)
async def invalid_evidence(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    example_id: str


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "mode": "synthetic_research_workbench",
        "models_trained": (ROOT / "artifacts/gnn_model.json").exists(),
        "durable_service_configured": bool(os.getenv("DATABASE_URL")),
        "private_review_configured": all(
            os.getenv(key) for key in ("DATABASE_URL", "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL")
        ),
        "integrations": {
            "neptune": "worker configuration",
            "kafka": "worker configuration",
            "live_screening": False,
            "llm": False,
        },
    }


@app.get("/api/cases")
def demo_cases():
    return CASES


@app.get("/api/ontology")
def ontology():
    return ontology_schema()


@app.get("/api/explorer/{case_id}")
def graph_explorer(case_id: str, variant: Variant = "valid", query: Query = "transactions"):
    try:
        return explore(case_id, variant, query)
    except KeyError:
        raise HTTPException(404, "Unknown case") from None


@app.post("/api/investigate")
def run(body: InvestigationRequest):
    case = next((case for case in CASES if case["id"] == body.case_id), None)
    if not case:
        raise HTTPException(404, "Unknown case")
    return public_result(investigate(case))


@app.get("/api/model/examples")
def model_examples():
    return [
        {
            key: value
            for key, value in item.items()
            if key
            not in {"head_input", "reference_score", "baseline_input", "baseline_reference_scores"}
        }
        for item in examples()
    ]


@app.post("/api/model/score")
def model_score(body: ModelRequest):
    try:
        return predict_example(body.example_id)
    except KeyError:
        raise HTTPException(404, "Unknown benchmark example") from None


@app.get("/api/model/evaluation")
def evaluation():
    return json.loads((ROOT / "artifacts/evaluation.json").read_text())


def public_result(result):
    return {key: value for key, value in result.items() if key != "workflow_state"}


@app.post("/api/v1/events", status_code=201)
def ingest(body: CaseEvent, user: Principal = Depends(principal)):
    user.require("ingest")
    if body.tenant_id != user.tenant:
        raise HTTPException(403, "Tenant mismatch")
    return public_result(InvestigationService(store()).ingest(body))


@app.get("/api/v1/investigations")
def list_investigations(user: Principal = Depends(principal)):
    user.require("analyst")
    return [public_result(result) for result in store().list_cases(user.tenant)]


@app.get("/api/v1/investigations/{run_id}")
def get_investigation(run_id: str, user: Principal = Depends(principal)):
    user.require("analyst")
    return public_result(store().get(user.tenant, run_id))


@app.post("/api/v1/investigations/{run_id}/reviews")
def review(run_id: str, body: Review, user: Principal = Depends(principal)):
    user.require("reviewer")
    return public_result(InvestigationService(store()).review(user.tenant, run_id, user.subject, body))


@app.get("/api/v1/investigations/{run_id}/audit")
def audit_trail(run_id: str, user: Principal = Depends(principal)):
    user.require("analyst")
    return store().audit_trail(user.tenant, run_id)


@app.get("/")
def home():
    return FileResponse(ROOT / "index.html")


# An allowlist avoids exposing source code, credentials, or a raw data directory.
@app.get("/{asset:path}")
def static_asset(asset: str):
    allowed = {
        "app.js",
        "style.css",
        "research.json",
        "guide.json",
        "lab.js",
        "architecture.svg",
        "docs/FinGraph_Research_and_Architecture.html",
        *DEMO_ASSETS,
    }
    if asset not in allowed:
        raise HTTPException(404, "Unknown asset")
    return FileResponse(ROOT / asset)
