# Verification record

Version 0.3, 19 November 2026.

## Completed checks

- Local regression suite: **36 tests passed**. The suite covers replay, conflicting payloads, restart recovery, versioned reviews, concurrent reviewer conflicts, SQL rollback, tenant-scoped HTTP access, OIDC signatures and expiry, quarantine, projection retries, and model export parity.
- The expanded explorer tests cover all five fixtures, three invalid-graph mutations per fixture, unchanged source records, exact source provenance, and rejection of unlisted query/variant names.
- Explanation tests reconstruct the logistic score from its additive terms, check tree sensitivity differences and verify conservation of held-out confusion and review-budget counts.
- Ruff lint and formatting, Python compilation and JavaScript syntax checks are required in CI.
- The first complete hosted pipeline passed both the code-check and actual Kafka/PostgreSQL jobs: [CI run 44250981883](https://github.com/deepak2233/fingraph-investigations/actions/runs/44250981883), commit `157b4cc41dffdd2d3fce39a990fb5630c2920b23`. That run includes ingestion, projection, durable review, worker restart and duplicate replay.

## Model verification

Three models were trained on a 5% label-blind sample of IBM AMLworld HI-Small. The source checksum, sampling parameters, temporal boundaries, metrics, runtime versions and selected epoch are retained in `artifacts/evaluation.json`. The expanded run adds PR/ROC display points, review-budget counts, confusion matrices, reliability bins and validation-only grouped permutation diagnostics. It uses the same seed and fixed model settings. Model exports match their training-runtime prediction references within absolute tolerance 0.000001.

## Limits of the evidence

Neptune transport is tested against a mocked HTTP endpoint for SigV4 and tenant-specific graph URIs; a live Neptune cluster has not been exercised. Public investigation review is browser-session only. Durable review requires a configured database and identity provider. No live sanctions feed is connected.

The previous browser verification attempt was blocked by an automatic approval-review usage limit. The expanded UI has not yet received a completed visual browser check. Current build and API checks should not be represented as a visual acceptance test.

Vercel deployment is performed using the connected deployment service. The optional manual GitHub deployment workflow requires repository secrets/variables; their configuration has not been established. Training and service integration are reproducible from the repository. Production load, independent security testing, disaster recovery and real-bank model validation remain unverified.
