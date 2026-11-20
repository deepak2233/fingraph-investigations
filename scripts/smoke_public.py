"""Check the public URL without credentials or preview bypass cookies."""

import argparse
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def verify(base):
    origin = urlsplit(base).netloc

    def fetch(path, media_type, payload=None):
        request = Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        with urlopen(request, timeout=15) as response:
            if response.status != 200 or urlsplit(response.geturl()).netloc != origin:
                raise RuntimeError(f"{path} redirected or returned HTTP {response.status}")
            if response.headers.get_content_type() != media_type:
                raise RuntimeError(f"{path} returned {response.headers.get('Content-Type')}")
            body = response.read()
        print(f"OK {path}", flush=True)
        return json.loads(body) if media_type == "application/json" else body

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    home = fetch("/", "text/html")
    require(b"Start with Rapid fan-out" in home, "The public landing page is missing its start section")
    architecture = fetch("/docs/FinGraph_Research_and_Architecture.html", "text/html")
    health = fetch("/api/health", "application/json")
    require(health["status"] == "ok" and health["models_trained"], "The live API is not ready")
    version = health["version"]
    require(
        all(f'/{script}.js?v={version}"'.encode() in home for script in ("app", "lab"))
        and f"FinGraph v{version}".encode() in architecture,
        "The workbench, architecture report, and live API have different release versions",
    )
    catalog = fetch(f"/demo/catalog.json?v={version}", "application/json")
    require(len(catalog["cases"]) == 5 and catalog["guide"]["overview"], "Demo catalog is incomplete")
    rolling = catalog["rolling_benchmark"]
    handoffs = [case for fold in rolling["folds"] for case in fold.get("shadow_handoff", [])]
    require(
        rolling["source_rows"] == 5_078_345
        and len(rolling["folds"]) == 2
        and len(handoffs) == 3
        and all(case["shacl_conforms"] and case["audit_records_after_replay"] == 1 for case in handoffs),
        "The full-history comparison or shadow handoff is missing from Model Lab",
    )
    for case in catalog["cases"]:
        case_id = case["id"]
        published = fetch(f"/demo/{case_id}.json?v={version}", "application/json")["variants"]
        require(
            published["valid"]["graph"]["validation"]["conforms"], f"{case_id} saved graph failed SHACL"
        )
        require(
            not published["missing_evidence"]["graph"]["validation"]["conforms"],
            f"{case_id} saved validation exercise did not fail as expected",
        )
        live = fetch(
            f"/api/explorer?action=rdf-explorer&case_id={case_id}&variant=valid&query=transactions",
            "application/json",
        )
        require(
            live["case_id"] == case_id
            and live["validation"]["conforms"]
            and live["query"] == published["valid"]["queries"]["transactions"],
            f"{case_id} live graph did not match its saved transaction results",
        )
    case_id = catalog["cases"][0]["id"]
    selection = f"action=rdf-explorer&case_id={case_id}&variant=missing_evidence&query=ownership"
    graph = fetch(f"/api/explorer?{selection}", "application/json")
    require(not graph["validation"]["conforms"], "The live explorer accepted a missing evidence link")
    entry = fetch(f"/api/index.py?{selection}", "application/json")
    require(entry == graph, "The live function entry returned a different graph")
    result = fetch("/api/investigate", "application/json", {"case_id": case_id})
    require(
        result["status"] == "awaiting_analyst_review" and len(result["trace"]) == 5, "Workflow failed"
    )
    example = catalog["examples"][0]["id"]
    reference = fetch(f"/demo/scores.json?v={version}", "application/json")[example]
    scores = fetch("/api/model/score", "application/json", {"example_id": example})
    require(
        scores["scores"] == reference["scores"] and len(scores["scores"]) == 3, "Unexpected model scores"
    )
    require("logistic" in scores["explanations"], "Missing score explanations")
    graphsage = scores["explanations"]["graphsage"]
    reconstructed = graphsage["reference_logit"] + sum(
        row["logit_contribution"] for row in graphsage["groups"]
    )
    require(
        abs(reconstructed - graphsage["logit"]) < 1e-12, "GraphSAGE contributions do not sum to logit"
    )
    print(f"Public workbench and live demo verified (API {health['version']}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "url", help="Public production origin, e.g. https://fingraph-investigations.vercel.app"
    )
    verify(parser.parse_args().url.rstrip("/"))
