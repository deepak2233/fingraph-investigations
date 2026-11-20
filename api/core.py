"""Executable research prototype. No learned model, real screening, or filing integration."""

from __future__ import annotations
from collections import defaultdict
from bisect import bisect_right
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TypedDict
import json
from rdflib import Graph, Namespace, Literal, RDF, XSD
from pyshacl import validate
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
from pydantic import ValidationError
from pipeline.contracts import InvestigationCase

ROOT = Path(__file__).resolve().parents[1]
FG = Namespace("https://fingraph.example/ontology/")
EX = Namespace("https://fingraph.example/data/")
CASES = json.loads((ROOT / "data/cases.json").read_text())
SHAPES = (ROOT / "ontology/shapes.ttl").read_text()
ONTOLOGY = (ROOT / "ontology/fingraph.ttl").read_text()
POLICY_VERSION = "rules-0.2"


def graph_for(case: dict) -> Graph:
    g = Graph().parse(data=ONTOLOGY, format="turtle")
    for a in case["accounts"]:
        g.add((EX[a["id"]], RDF.type, FG.Account))
        g.add((EX[a["id"]], FG.heldBy, EX[a["holder"]]))
        g.add((EX[a["holder"]], RDF.type, FG.LegalEntity))
        g.add((EX[a["holder"]], RDF.type, FG.Party))
        if not a.get("owner") or (case["missing_ownership"] and a["id"] == "A001"):
            continue
        p, entity = EX[a["owner"]], EX[a["holder"]]
        claim, evidence = EX["ownership-" + a["id"]], EX["registry-" + a["id"]]
        g.add((p, RDF.type, FG.Person))
        g.add((p, RDF.type, FG.Party))
        for triple in [
            (claim, RDF.type, FG.OwnershipAssertion),
            (claim, FG.owner, p),
            (claim, FG.ownedEntity, entity),
            (claim, FG.percentage, Literal(Decimal(a["owner_percentage"]), datatype=XSD.decimal)),
            (claim, FG.supportedBy, evidence),
            (evidence, RDF.type, FG.Evidence),
            (evidence, FG.sourceId, Literal(a["ownership_source"])),
            (evidence, FG.occurredAt, Literal(a["ownership_observed_at"], datatype=XSD.dateTime)),
        ]:
            g.add(triple)
    for t in case["transactions"]:
        s = EX[t["id"]]
        for triple in [
            (s, RDF.type, FG.Transaction),
            (s, FG.sender, EX[t["sender"]]),
            (s, FG.receiver, EX[t["receiver"]]),
            (s, FG.amount, Literal(Decimal(t["amount"]), datatype=XSD.decimal)),
            (s, FG.currency, Literal(t["currency"])),
            (s, FG.occurredAt, Literal(t["timestamp"], datatype=XSD.dateTime)),
            (s, FG.sourceId, Literal(t["source_id"])),
        ]:
            g.add(triple)
    return g


def validate_graph(g: Graph) -> dict:
    conforms, report, _ = validate(
        g, shacl_graph=Graph().parse(data=SHAPES, format="turtle"), inference="rdfs"
    )
    SH = Namespace("http://www.w3.org/ns/shacl#")
    issues = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        issues.append(
            {
                "focus": str(report.value(result, SH.focusNode)),
                "path": str(report.value(result, SH.resultPath)),
                "message": str(report.value(result, SH.resultMessage)),
            }
        )
    return {
        "conforms": bool(conforms),
        "issues": issues,
        "engine": "pySHACL / RDFS closure",
        "owl_reasoning": "Full OWL DL reasoning is not enabled",
    }


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def risk_signals(case: dict) -> list[dict]:
    signals = []
    txs = sorted(case["transactions"], key=lambda t: dt(t["timestamp"]))
    by_sender = defaultdict(list)
    for t in txs:
        by_sender[t["sender"]].append(t)
    for sender, rows in by_sender.items():
        for first in rows:
            window = [
                t
                for t in rows
                if 0 <= (dt(t["timestamp"]) - dt(first["timestamp"])).total_seconds() <= 3600
            ]
            if len({t["receiver"] for t in window}) >= 4:
                signals.append(
                    {
                        "id": "S-FANOUT",
                        "type": "rapid_fan_out",
                        "points": 40,
                        "description": f"{sender} paid {len(set(t['receiver'] for t in window))} distinct accounts within 60 minutes. This can also occur in legitimate treasury activity.",
                        "evidence": [t["id"] for t in window],
                    }
                )
                break
    # Adjacency join with a sorted closing-edge index avoids a cubic scan.
    by_pair = defaultdict(list)
    for transaction in txs:
        by_pair[(transaction["sender"], transaction["receiver"], transaction["currency"])].append(
            transaction
        )
    closing_times = {pair: [dt(t["timestamp"]) for t in rows] for pair, rows in by_pair.items()}
    for first in txs:
        for second in by_sender.get(first["receiver"], []):
            first_time, second_time = dt(first["timestamp"]), dt(second["timestamp"])
            if first["currency"] != second["currency"] or not first_time < second_time:
                continue
            if len({first["sender"], first["receiver"], second["receiver"]}) < 3:
                continue
            pair = (second["receiver"], first["sender"], first["currency"])
            index = bisect_right(closing_times.get(pair, []), second_time)
            candidates = by_pair.get(pair, [])
            if index < len(candidates):
                third = candidates[index]
                if (dt(third["timestamp"]) - first_time).total_seconds() <= 86400:
                    signals.append(
                        {
                            "id": "S-CYCLE",
                            "type": "circular_flow",
                            "points": 45,
                            "description": "A chronological three-account cycle occurs within 24 hours. Investigate its economic purpose and source records.",
                            "evidence": [first["id"], second["id"], third["id"]],
                        }
                    )
                    return signals
    return signals


class State(TypedDict, total=False):
    case: dict
    signals: list[dict]
    screening: dict
    ownership: dict
    evidence: list[dict]
    trace: list[dict]
    draft: dict
    decision: dict


def trace(s, name, result):
    return s.get("trace", []) + [{"agent": name, "result": result}]


def graph_agent(s):
    sig = risk_signals(s["case"])
    return {
        "signals": sig,
        "trace": trace(s, "Graph risk", f"{len(sig)} configured motif(s); no GNN invoked"),
    }


def screening_agent(s):
    stale = s["case"]["stale_screening"]
    screening = {
        "status": "stale" if stale else "demo_only",
        "candidate_matches": [],
        "snapshot": "synthetic-list-2026-11-01" if not stale else "synthetic-list-2026-01-01",
        "source": "Authored empty screening fixture, not a sanctions list",
        "conclusion": "Live screening unavailable; no clearance can be inferred",
    }
    return {
        "screening": screening,
        "trace": trace(
            s,
            "Screening",
            "Stale fixture; refresh required" if stale else "Fixture only; live screening required",
        ),
    }


def ownership_agent(s):
    g = graph_for(s["case"])
    query = """PREFIX fg: <https://fingraph.example/ontology/>
    SELECT ?account ?entity ?owner ?claim ?evidence ?source ?observed WHERE {
      ?account a fg:Account ; fg:heldBy ?entity .
      ?claim a fg:OwnershipAssertion ; fg:ownedEntity ?entity ; fg:owner ?owner ; fg:supportedBy ?evidence .
      ?evidence fg:sourceId ?source ; fg:occurredAt ?observed .
    } ORDER BY ?account ?claim"""
    rows = [
        {
            **{
                key: str(row[key]).rsplit("/", 1)[-1]
                for key in ["account", "entity", "owner", "claim", "evidence"]
            },
            "source": str(row.source),
            "observed_at": str(row.observed),
        }
        for row in g.query(query)
    ]
    relevant = {t[k] for t in s["case"]["transactions"] for k in ("sender", "receiver")}
    missing = sorted(relevant - {r["account"] for r in rows})
    owners = defaultdict(set)
    for r in rows:
        if r["account"] in relevant:
            owners[r["owner"]].add(r["account"])
    shared = [{"owner": p, "accounts": sorted(a)} for p, a in owners.items() if len(a) > 1]
    return {
        "ownership": {
            "assertions": rows,
            "missing": missing,
            "shared": shared,
            "scope": "Direct source-backed ownership assertions",
        },
        "trace": trace(
            s,
            "Ownership",
            f"{len(rows)} source-backed direct assertions; {len(missing)} relevant gap(s)",
        ),
    }


def evidence_agent(s):
    evidence = [
        {
            "id": t["id"],
            "source": t["source_id"],
            "kind": "transaction",
            "text": f"{t['sender']} → {t['receiver']}: {t['currency']} {t['amount']} at {t['timestamp']}",
        }
        for t in s["case"]["transactions"]
    ]
    # One claim can apply to several accounts held by the same legal entity.
    ownership_evidence = {
        row["evidence"]: {
            "id": row["evidence"],
            "source": row["source"],
            "kind": "ownership",
            "text": f"{row['owner']} is listed as a direct owner of {row['entity']}.",
        }
        for row in s["ownership"]["assertions"]
    }
    evidence.extend(ownership_evidence.values())
    return {
        "evidence": evidence,
        "trace": trace(s, "Evidence", f"{len(evidence)} source references collected"),
    }


def drafting_agent(s):
    score = min(100, sum(x["points"] for x in s["signals"]))
    findings = [{"text": x["description"], "evidence": x["evidence"]} for x in s["signals"]]
    if not findings:
        findings = [
            {
                "text": "No configured motif was detected. This does not establish that activity is legitimate.",
                "evidence": [t["id"] for t in s["case"]["transactions"]],
            }
        ]
    known = {e["id"] for e in s["evidence"]}
    if any(e not in known for f in findings for e in f["evidence"]):
        raise ValueError("Unresolved evidence reference")
    gaps = [
        "Live screening not connected.",
        "Customer purpose and source of funds not independently verified.",
    ]
    if s["ownership"]["missing"]:
        gaps.append("Missing source-backed ownership for " + ", ".join(s["ownership"]["missing"]) + ".")
    if s["screening"]["status"] == "stale":
        gaps.append("Screening fixture is stale; refresh before any operational decision.")
    draft = {
        "title": s["case"]["title"],
        "status": "awaiting_analyst_review",
        "priority": "review" if score else "routine",
        "heuristic_score": score,
        "score_meaning": "Demo prioritisation points, not a probability or ML output",
        "findings": findings,
        "gaps": gaps,
        "next_steps": [
            "Verify customer purpose and economic rationale.",
            "Refresh screening using an approved provider.",
            "Review source documents and resolve data gaps.",
        ],
        "filing": "No regulatory report generated or submitted.",
        "generator": "deterministic evidence-bound template v1",
    }
    return {
        "draft": draft,
        "trace": trace(
            s, "Case drafting", "Evidence references resolved; draft paused for analyst review"
        ),
    }


def review_gate(s):
    decision = interrupt({"kind": "analyst_review", "case_id": s["case"]["id"], "draft": s["draft"]})
    if (
        decision.get("action") not in ("approve", "reject", "request_evidence")
        or not decision.get("reviewer")
        or not decision.get("reason")
    ):
        raise ValueError("Reviewer, reason, and a valid action are required")
    return {"decision": decision}


def build_workflow():
    g = StateGraph(State)
    for name, fn in [
        ("graph_risk", graph_agent),
        ("screening", screening_agent),
        ("ownership", ownership_agent),
        ("evidence", evidence_agent),
        ("draft", drafting_agent),
        ("review", review_gate),
    ]:
        g.add_node(name, fn)
    chain = [START, "graph_risk", "screening", "ownership", "evidence", "draft", "review", END]
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    return g.compile(checkpointer=InMemorySaver())


def investigate(case: dict) -> dict:
    try:
        case = InvestigationCase.model_validate(case).model_dump(mode="json")
    except ValidationError as exc:
        return {
            "status": "quarantined",
            "validation": {
                "conforms": False,
                "issues": [
                    {"path": str(error["loc"]), "message": error["msg"]} for error in exc.errors()
                ],
            },
        }
    validation = validate_graph(graph_for(case))
    if not validation["conforms"]:
        return {"status": "quarantined", "validation": validation}
    graph = build_workflow()
    config = {"configurable": {"thread_id": case["id"]}}
    graph.invoke({"case": case, "trace": []}, config)
    state = dict(graph.get_state(config).values)
    fingerprint = sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
    return {
        "case_id": case["id"],
        "status": "awaiting_analyst_review",
        "validation": validation,
        "signals": state["signals"],
        "ownership": state["ownership"],
        "screening": state["screening"],
        "evidence": state["evidence"],
        "trace": state["trace"],
        "draft": state["draft"],
        "provenance": {
            "case_sha256": fingerprint,
            "ontology_version": "0.1.0",
            "policy_version": POLICY_VERSION,
            "data_origin": case["data_origin"],
            "as_of": case["as_of"],
            "review_storage": "Browser-session demonstration only; server checkpoints are request-local",
            "trained_model": None,
        },
        "workflow_state": state,
    }


def resume_review(result: dict, decision: dict) -> dict:
    """Restore the persisted pre-review snapshot, then resume the LangGraph gate.

    Specialist nodes are pure computations. Recovery reconstitutes the saved
    draft without repeating side effects. SQL owns the final versioned commit.
    """
    graph = build_workflow()
    config = {"configurable": {"thread_id": result["run_id"]}}
    graph.update_state(config, result["workflow_state"], as_node="draft")
    paused = graph.invoke(None, config)
    if "__interrupt__" not in paused:
        raise RuntimeError("Review checkpoint could not be restored")
    return graph.invoke(Command(resume=decision), config)["decision"]
