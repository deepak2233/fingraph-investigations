"""SigV4-authenticated RDF projection with tenant-specific named graphs."""

from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import httpx

from api.core import graph_for
from pipeline.store import digest


def graph_name(tenant, run_id):
    return f"urn:fingraph:tenant:{digest(tenant)}:run:{digest(run_id)}"


def insertion(tenant, run_id, case):
    graph = graph_for(case)
    triples = graph.serialize(format="nt")
    return f"INSERT DATA {{ GRAPH <{graph_name(tenant, run_id)}> {{\n{triples}\n}} }}"


class NeptuneProjection:
    def __init__(self, endpoint, region, session=None, client=None):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Neptune endpoint must be an HTTPS origin")
        self.endpoint = endpoint.rstrip("/") + "/sparql"
        self.region = region
        self.session = session or boto3.Session()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=5), follow_redirects=False
        )

    def write(self, tenant, run_id, case):
        # RDFLib produces escaped N-Triples. Identifiers used in graph names are hashes.
        body = insertion(tenant, run_id, case).encode()
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials unavailable")
        request = AWSRequest(
            method="POST",
            url=self.endpoint,
            data=body,
            headers={"Content-Type": "application/sparql-update"},
        )
        SigV4Auth(credentials.get_frozen_credentials(), "neptune-db", self.region).add_auth(request)
        response = self.client.post(self.endpoint, content=body, headers=dict(request.headers))
        response.raise_for_status()


class FileProjection:
    """Local equivalent of the per-tenant named-graph export."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, tenant, run_id, case):
        target = self.directory / f"{digest([tenant, run_id])}.nt"
        # Same immutable content on redelivery. Replace avoids partial output.
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as handle:
            handle.write(graph_for(case).serialize(format="nt"))
            temporary = handle.name
        os.replace(temporary, target)


def project_pending(store, projection):
    import json

    completed = 0
    for row in store.pending_projections():
        try:
            projection.write(row["tenant"], row["case_id"], json.loads(row["payload"]))
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            store.projection_result(row["id"], type(exc).__name__)
        else:
            store.projection_result(row["id"])
            completed += 1
    return completed
