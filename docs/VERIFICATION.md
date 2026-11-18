# Verification record

Version 0.2, 18 November 2026.

The local regression suite verifies ingestion replay, conflicting payloads, restart recovery, versioned reviews, concurrent reviewer conflicts, SQL rollback, tenant-scoped HTTP access, OIDC signature and expiry checks, quarantined records, graph projection retries, and model export parity. The existing semantic and workflow tests cover valid and invalid SHACL graphs, temporal cycles, ownership gaps, screening freshness, and analyst interruption.

Three models were trained on a 5% label-blind sample of IBM AMLworld HI-Small. The source checksum, sampling parameters, temporal boundaries, measured metrics, runtime versions, and selected epoch are retained in `artifacts/evaluation.json`. Model exports match their training-runtime prediction references within an absolute tolerance of 0.000001.

The Neptune transport test uses a mocked HTTP endpoint and checks the AWS SigV4 request and tenant-specific graph URI. A live Neptune environment has not been exercised. Public investigation review is local to the browser session. Durable review is available through the authenticated service API with configured database and identity provider.

GitHub CI and deployment outcomes are appended after the hosted verification completes.
