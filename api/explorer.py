"""Inspectable RDF projections and bounded validation exercises over public fixtures."""

from copy import deepcopy
from typing import Literal as Choice

from rdflib import Graph, Literal, RDF, RDFS, URIRef, XSD
from rdflib.namespace import OWL

from api.core import CASES, EX, FG, ONTOLOGY, SHAPES, graph_for, validate_graph

Variant = Choice["valid", "negative_amount", "missing_sender", "missing_evidence"]
Query = Choice["transactions", "ownership", "shared_owners"]
QUERIES = {
    "transactions": """PREFIX fg: <https://fingraph.example/ontology/>
SELECT ?transaction ?sender ?receiver ?amount ?currency ?source WHERE {
  ?transaction a fg:Transaction ; fg:sender ?sender ; fg:receiver ?receiver ;
    fg:amount ?amount ; fg:currency ?currency ; fg:sourceId ?source .
} ORDER BY ?transaction LIMIT 100""",
    "ownership": """PREFIX fg: <https://fingraph.example/ontology/>
SELECT ?account ?entity ?owner ?percentage ?claim ?source WHERE {
  ?account a fg:Account ; fg:heldBy ?entity .
  ?claim a fg:OwnershipAssertion ; fg:ownedEntity ?entity ; fg:owner ?owner ;
    fg:percentage ?percentage ; fg:supportedBy ?evidence .
  ?evidence fg:sourceId ?source .
} ORDER BY ?account LIMIT 100""",
    "shared_owners": """PREFIX fg: <https://fingraph.example/ontology/>
SELECT ?owner (COUNT(DISTINCT ?account) AS ?accounts) WHERE {
  ?account a fg:Account ; fg:heldBy ?entity .
  ?claim a fg:OwnershipAssertion ; fg:ownedEntity ?entity ; fg:owner ?owner .
} GROUP BY ?owner HAVING (COUNT(DISTINCT ?account) > 1) LIMIT 100""",
}


def compact(value):
    text = str(value)
    for namespace, prefix in [
        (EX, "ex"),
        (FG, "fg"),
        (RDF, "rdf"),
        (RDFS, "rdfs"),
        (XSD, "xsd"),
        (OWL, "owl"),
    ]:
        if text.startswith(str(namespace)):
            return prefix + ":" + text[len(str(namespace)) :]
    return text


def ontology_schema():
    graph = Graph().parse(data=ONTOLOGY, format="turtle")
    classes = sorted(compact(s) for s in graph.subjects(RDF.type, OWL.Class))
    properties = []
    for kind in [OWL.ObjectProperty, OWL.DatatypeProperty]:
        for subject in graph.subjects(RDF.type, kind):
            properties.append(
                {
                    "name": compact(subject),
                    "kind": compact(kind),
                    "domain": compact(graph.value(subject, RDFS.domain) or "Unspecified"),
                    "range": compact(graph.value(subject, RDFS.range) or "Unspecified"),
                }
            )
    return {
        "classes": classes,
        "properties": sorted(properties, key=lambda p: p["name"]),
        "ontology_turtle": ONTOLOGY,
        "shapes_turtle": SHAPES,
    }


def explore(case_id: str, variant: Variant = "valid", query: Query = "transactions"):
    case = next((deepcopy(item) for item in CASES if item["id"] == case_id), None)
    if case is None:
        raise KeyError(case_id)
    graph = graph_for(case)
    transaction = EX[case["transactions"][0]["id"]]
    mutation = "Original fixture graph. No mutation."
    if variant == "negative_amount":
        graph.set((transaction, FG.amount, Literal(-1, datatype=XSD.decimal)))
        mutation = f"Set {compact(transaction)} fg:amount to -1. Source JSON is unchanged."
    elif variant == "missing_sender":
        graph.remove((transaction, FG.sender, None))
        mutation = f"Remove {compact(transaction)} fg:sender. Source JSON is unchanged."
    elif variant == "missing_evidence":
        claim = sorted(graph.subjects(RDF.type, FG.OwnershipAssertion))[0]
        graph.remove((claim, FG.supportedBy, None))
        mutation = f"Remove {compact(claim)} fg:supportedBy. Source JSON is unchanged."
    validation = validate_graph(graph)
    triples = sorted((s, p, o) for s, p, o in graph if str(s).startswith(str(EX)))
    nodes = sorted(
        {s for s, _, _ in triples}
        | {
            o
            for _, p, o in triples
            if p != RDF.type and isinstance(o, URIRef) and str(o).startswith(str(EX))
        }
    )
    rows = [
        {str(key): compact(value) for key, value in row.asdict().items()}
        for row in graph.query(QUERIES[query])
    ]
    return {
        "case_id": case_id,
        "source": case,
        "variant": variant,
        "mutation": mutation,
        "validation": validation,
        "instance_triple_count": len(triples),
        "schema_triple_count": len(graph) - len(triples),
        "nodes": [
            {
                "id": compact(node),
                "iri": str(node),
                "types": sorted(compact(t) for t in graph.objects(node, RDF.type)),
            }
            for node in nodes
        ],
        "links": [
            {"source": compact(s), "predicate": compact(p), "target": compact(o)}
            for s, p, o in triples
            if p != RDF.type and isinstance(o, URIRef)
        ],
        "triples": [
            {
                "subject": compact(s),
                "predicate": compact(p),
                "object": compact(o),
                "literal": isinstance(o, Literal),
                "datatype": compact(o.datatype) if isinstance(o, Literal) and o.datatype else None,
            }
            for s, p, o in triples
        ],
        "turtle": graph.serialize(format="turtle"),
        "query": {
            "id": query,
            "sparql": QUERIES[query],
            "rows": rows,
            "allowed_for_investigation": validation["conforms"],
        },
        "scope": "Read-only educational query on a synthetic graph. Invalid graphs are inspectable here, but blocked from investigation.",
    }
