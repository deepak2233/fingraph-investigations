# Verification record

Version 0.3.1, updated 20 November 2026.

## Public access and startup

An unauthenticated HTTP client reached the production origin and public endpoints. Browser inspection found an avoidable startup dependency: the overview was hidden until JavaScript ran, and separate sections waited for batches of API calls before rendering. A slow or failed function could leave a section empty with the error shown elsewhere.

The release renders a landing page and an architecture report link before JavaScript runs. It loads a static catalog and the first graph before any Python calls. Explicit investigation, validation and scoring still use the live API. Published results have a visible origin and provide a fallback on transport failures, timeouts, rate limits and server errors. Authentication failures remain errors. The private review form appears only when database and OIDC settings are present.

`python -m scripts.export_workbench --check` checks the published catalog and results against their source implementations. `python -m scripts.smoke_public https://fingraph-investigations.vercel.app` checks the public origin without credentials and runs the graph, workflow and model calls end to end. The deployment workflow uses the public alias for this probe because generated Vercel deployment URLs can be protected.

## Completed checks

- Local regression suite: **38 tests passed**. The suite covers replay, conflicting payloads, restart recovery, versioned reviews, concurrent reviewer conflicts, SQL rollback, tenant-scoped HTTP access, OIDC signatures and expiry, quarantine, projection retries, and model export parity.
- The expanded explorer tests cover all five fixtures, three invalid-graph mutations per fixture, unchanged source records, exact source provenance, and rejection of unlisted query/variant names.
- Explanation tests reconstruct the logistic score from its additive terms, check tree sensitivity differences and verify conservation of held-out confusion and review-budget counts.
- A plot regression test integrates the exported PR steps and ROC segments for all three models on validation and test partitions. Both areas must reproduce their reported metrics within 0.000000000001.
- The runtime-manifest check prevents Vercel dependency discovery from omitting the required Python packages.
- Ruff lint and formatting, Python compilation and JavaScript syntax checks are required in CI.
- The hosted pipeline passed both the code-check and actual Kafka/PostgreSQL jobs: [CI run 44523830545](https://github.com/deepak2233/fingraph-investigations/actions/runs/44523830545), commit `d2e224c10327a31060b5ae2f87ef23f5df69c5f5`. That run includes ingestion, projection, durable review, worker restart and duplicate replay.

## Model verification

Three models were trained on a 5% label-blind sample of IBM AMLworld HI-Small. The source checksum, sampling parameters, temporal boundaries, metrics, runtime versions and selected epoch are retained in `artifacts/evaluation.json`. The expanded run adds PR/ROC display points, review-budget counts, confusion matrices, reliability bins and validation-only grouped permutation diagnostics. It uses the same seed and fixed model settings. Model exports match their training-runtime prediction references within absolute tolerance 0.000001.

## Live application checks

The expanded Vercel workbench was exercised through a real browser on 19–20 November 2026:

- Source tables and 110 asserted triples loaded for the rapid fan-out fixture. Selecting an ownership assertion displayed its legal entity, owner, percentage and supporting registry record.
- Removing an ownership evidence link caused a live SHACL failure on `supportedBy`. The ownership SPARQL results excluded the unsupported assertion; source records remained unchanged.
- The investigation produced the four-transfer fan-out finding, linked evidence and an analyst-review gate.
- All three models returned live scores for a held-out example. Logistic contributions reconstructed its logit and score.
- Changing review capacity from 1% to 10% updated the review counts, false positives and misses. Selecting GraphSAGE updated its validation-threshold confusion matrix.
- Goal/scope cards, ontology construction steps, the actual vocabulary and the research filter rendered. Filtering GraphSAGE returned two relevant paper records.

The check found and fixed two display defects: untyped string literals were labelled as resources, and uniform curve subsampling distorted the high-score end of the PR plot. Curve exports now preserve recall/TPR transitions. Regenerating the experiment with the same settings left headline AP, ROC AUC and review-budget results unchanged. Interactive SVG graphs expose their child controls, and upper-node labels sit clear of edge captions.

A prior Vercel build had reported ready while API calls failed because the Python project manifest did not declare its runtime dependencies. The manifest now declares them explicitly. The corrected deployment returned successful live investigation, explorer and inference results.

## Limits of the evidence

Neptune transport is tested against a mocked HTTP endpoint for SigV4 and tenant-specific graph URIs; a live Neptune cluster has not been exercised. Public investigation review is browser-session only. Durable review requires a configured database and identity provider. No live sanctions feed is connected.

Vercel deployment is performed using the connected deployment service. The optional manual GitHub deployment workflow requires repository secrets/variables; their configuration has not been established. Training and service integration are reproducible from the repository. Production load, independent security testing, disaster recovery and real-bank model validation remain unverified.

For subsequent changes, inspect the [current CI runs](https://github.com/deepak2233/fingraph-investigations/actions) and their commit SHA. A past passing run is not evidence that an unrelated later change passed.
