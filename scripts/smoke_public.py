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
    fetch("/docs/FinGraph_Research_and_Architecture.html", "text/html")
    catalog = fetch("/demo/catalog.json", "application/json")
    require(len(catalog["cases"]) == 5 and catalog["guide"]["overview"], "Demo catalog is incomplete")
    case_id = catalog["cases"][0]["id"]
    sample = fetch(f"/demo/{case_id}.json", "application/json")
    require(
        sample["variants"]["valid"]["graph"]["validation"]["conforms"], "Published graph failed SHACL"
    )
    require(
        not sample["variants"]["missing_evidence"]["graph"]["validation"]["conforms"],
        "Published validation exercise did not fail as expected",
    )
    health = fetch("/api/health", "application/json")
    require(health["status"] == "ok" and health["models_trained"], "The live API is not ready")
    graph = fetch(
        f"/api/explorer/{case_id}?variant=missing_evidence&query=ownership", "application/json"
    )
    require(not graph["validation"]["conforms"], "The live explorer accepted a missing evidence link")
    result = fetch("/api/investigate", "application/json", {"case_id": case_id})
    require(
        result["status"] == "awaiting_analyst_review" and len(result["trace"]) == 5, "Workflow failed"
    )
    example = catalog["examples"][0]["id"]
    reference = fetch("/demo/scores.json", "application/json")[example]
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
