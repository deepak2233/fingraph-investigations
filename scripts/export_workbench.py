"""Publish the public synthetic inputs and reference results from their source implementations."""

import argparse
import json
from pathlib import Path
from typing import get_args

from api.core import CASES, investigate
from api.explorer import Query, Variant, explore, ontology_schema
from api.index import evaluation, model_examples, public_result
from ml.inference import predict_example

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "demo"


def files():
    examples = model_examples()
    yield (
        "catalog.json",
        {
            "cases": CASES,
            "guide": json.loads((ROOT / "guide.json").read_text()),
            "research": json.loads((ROOT / "research.json").read_text()),
            "ontology": ontology_schema(),
            "evaluation": evaluation(),
            "examples": examples,
        },
    )
    yield "scores.json", {item["id"]: predict_example(item["id"]) for item in examples}
    first_query, *other_queries = get_args(Query)
    for case in CASES:
        case_id = case["id"]
        variants = {}
        for variant in get_args(Variant):
            graph = explore(case_id, variant, first_query)
            graph.pop("source")
            queries = {first_query: graph.pop("query")}
            for query in other_queries:
                queries[query] = explore(case_id, variant, query)["query"]
            variants[variant] = {"graph": graph, "queries": queries}
        yield (
            f"{case_id}.json",
            {"variants": variants, "investigation": public_result(investigate(case))},
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if committed demo files need regeneration"
    )
    args = parser.parse_args()
    if not args.check:
        OUTPUT.mkdir(exist_ok=True)
    stale = []
    for name, data in files():
        path = OUTPUT / name
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.write_text(content, encoding="utf-8")
    if stale:
        parser.exit(1, "Regenerate public demo assets: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
