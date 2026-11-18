# FinGraph research and system architecture

Deepak Yadav · Personal engineering project · Version 0.2 · 18 November 2026

FinGraph turns a bounded transaction snapshot into an investigation with traceable observations, ownership evidence, and a recorded human review. The repository implements candidate generation, Kafka intake, semantic validation, a five-stage LangGraph workflow, durable SQL records, a retryable graph projection, three trained model baselines, and an inference workbench. The public Vercel app runs synthetic scenarios and benchmark inference. The private service stack runs with PostgreSQL, Kafka, and an OIDC provider.

## 1. The user and the decision

The intended user is an AML analyst at a bank or payment institution. An alert arrives because transaction behavior deserves review. The analyst needs to establish which records support the alert, whether account holders are connected, which observations remain uncertain, and what evidence should be requested next.

For example, Aster Trading sends four payments to separate counterparties within an hour. The graph-risk stage identifies the pattern. Ownership retrieval finds a shared direct owner assertion for two accounts. The evidence stage attaches the transaction IDs and registry references. The draft records the observed pattern and the open questions. An authenticated reviewer can record that the draft has been reviewed, reject it, or request further evidence. Investigation outcomes stay separate from the source ledger.

A production programme would measure analyst time, unsupported statements, evidence coverage, missed important information, and disagreement between reviewers. These outcomes require an analyst study. The current engineering verification measures software behavior and benchmark model predictions.

## 2. Architecture

![System architecture](../architecture.svg)

| Component | Responsibility | Executable entry point |
|---|---|---|
| Candidate builder | Validate a canonical ledger snapshot, apply event-time bounds, build rule-triggered cases | `python -m pipeline.candidates` |
| Publisher | Publish versioned case events with an idempotent Kafka producer | `python -m pipeline.cli publish` |
| Intake worker | Consume events, validate contracts, run the investigation, commit SQL, acknowledge Kafka | `python -m pipeline.worker` |
| Semantic engine | Map records to RDF, validate shapes, retrieve direct ownership context | `api/core.py` |
| Investigation workflow | Graph risk, screening availability, ownership, evidence, drafting, review gate | `api/core.py` |
| SQL ledger | Event deduplication, cases, review requests, audit chain, projection outbox, quarantine | `pipeline/store.py` |
| Graph projection | Write a case graph into a deterministic tenant-specific graph | `pipeline/neptune.py` |
| Private API | OIDC authentication, roles, tenant scoping, versioned review | `api/index.py` |
| Model pipeline | Dataset preparation, temporal snapshots, training, evaluation, JSON exports | `ml/prepare.py`, `ml/train.py` |
| Workbench | Investigation view, model comparison, inference, authenticated service controls | `index.html`, `app.js` |

The semantic investigation and ML benchmark are two executable paths with different inputs. The five authored investigation cases exercise evidence handling. IBM AMLworld supplies model training and evaluation. Integrating a continuously refreshed bank feature store and trained score into the investigation policy is a subsequent institutional deployment decision.

## 3. Source contracts and candidate generation

A ledger connector supplies resolved account identifiers, account holders, transaction amounts, currency, timestamps with timezones, and source record IDs. Direct ownership assertions additionally require their percentage, source reference, and observation time. Decimal monetary values retain their precision during ingestion and RDF construction.

`InvestigationCase` rejects duplicate account or transaction identifiers, missing referenced accounts, future-dated transactions relative to the cutoff, missing source IDs, invalid monetary values, and ownership observations from after the cutoff. The current bounded contract accepts up to 500 accounts and 1,000 transactions. HTTP bodies are limited to 2 MB by counting streamed bytes.

The candidate builder applies a configurable lookback of 1 to 168 hours. Its default is 24 hours. It detects fan-out and chronological three-account cycles, preserves one-hop context around the triggering records, and constructs deterministic event IDs. The current cycle policy emits the first qualifying cycle within a bounded snapshot. Large-scale transaction monitoring needs partitioning and a candidate-recall study before adopting this detector.

Each case event carries a schema version, tenant ID, immutable event ID, received-at timestamp, and case payload. The Kafka key combines tenant and case identity. Canonical bank adapters, identity resolution, and cross-bank data-sharing arrangements remain specific to the source institution.

## 4. Ontology and graph integrity

| Concept | Representation | Interpretation |
|---|---|---|
| Account holder | `Account → heldBy → Party` | Resolved source account ownership |
| Transfer | `Transaction → sender / receiver → Account` | A distinct payment event with decimal amount and timestamp |
| Ownership assertion | Owner, owned entity, percentage, supported-by evidence | A source assertion requiring date and provenance |
| Evidence | Source identifier and observation timestamp | The record supporting an assertion |
| Risk signal | Rule output with transaction references | A reason to investigate |
| Investigation | Frozen input, output, version, review state | The unit of analyst work |

RDF identifiers preserve source relationships. OWL supplies the vocabulary and class semantics. SHACL verifies required properties and datatypes. SPARQL retrieves direct ownership records. The current implementation runs RDFS closure for validation. Full OWL DL reasoning and legal beneficial-owner determination require further components and rules. [W3C OWL 2](https://www.w3.org/TR/owl2-overview/), [SHACL](https://www.w3.org/TR/shacl/), [SPARQL](https://www.w3.org/TR/sparql11-query/)

FIBO provides a relevant financial vocabulary for a future mapping. FinGraph uses its own compact namespace so the actual scope can be inspected. Any FIBO mapping should have competency questions, versioned alignments, and reviewed equivalence statements. PROV-O supplies a standard foundation for richer entity, activity, and agent provenance. [FIBO](https://edmcouncil.org/frameworks/industry-models/fibo/), [PROV-O](https://www.w3.org/TR/prov-o/)

Indirect ownership needs more than multiplying percentages along arbitrary paths. Cross-holdings, multiple valid paths, share classes, changing control rights, and effective dates can change the interpretation. The executable scope retrieves direct source assertions. Missing assertions become explicit gaps. FATF guidance motivates reliable beneficial-ownership information, while local legal interpretation remains institution-specific. [FATF beneficial-ownership guidance](https://www.fatf-gafi.org/en/publications/Fatfrecommendations/Guidance-Beneficial-Ownership-Legal-Persons.html)

## 5. Five specialist contracts

| Stage | Input | Output | Failure or uncertainty |
|---|---|---|---|
| Graph risk | Valid bounded transaction graph | Fan-out and chronological same-currency cycle signals | A pattern requires economic context |
| Screening | Screening fixture status | Availability and freshness information | Live sanctions screening remains unconnected |
| Ownership | Typed account-holder graph | Direct assertions, shared-owner context, missing ownership | Source gaps stay visible |
| Evidence | Transactions and retrieved assertions | Referenced source register | Unresolved references prevent a valid draft |
| Drafting | Signals, evidence, source gaps | Deterministic narrative and investigation questions | Analyst review remains required |

The stages are deterministic LangGraph nodes. They have bounded inputs and explicit result contracts. A generative model would add a separate evaluation burden for unsupported claims, prompt injection, and source entailment. The current narrative comes from templates and validated source references. ReAct and RAG inform the research comparison but are research references for this version.

The graph-risk implementation groups transfers by sender and indexes possible cycle-closing edges by sender, receiver, and currency. Binary search enforces strict transaction chronology. Amounts in different currencies are not summed. Self-transfers cannot form a three-account cycle. Legitimate treasury, payroll, and aggregator behavior can resemble the configured patterns, so the draft preserves the need for contextual review.

## 6. Durability and concurrency

The SQL ledger uses six tables. `intake_events` records canonical payloads and their SHA-256 fingerprints. `investigations` stores the completed workflow snapshot and current review version. `review_requests` records idempotent review responses. `audit_events` stores a chained sequence of changes. `outbox` holds graph projections awaiting publication. `quarantine` retains malformed source events with their Kafka coordinates.

Event insertion, case creation, the first audit record, and the graph outbox entry share one SQL transaction. Repeating an event ID with identical content returns its existing case. Reusing that ID with different content yields a conflict. A database failure rolls back the entire transaction.

A review includes an expected version and request ID. The reviewer identity comes from the verified token. The SQL update checks both the prior version and the awaiting-review status. Concurrent requests produce one committed winner. An exact retry returns the already recorded response. Changed content under an old request ID produces a conflict.

The immutable pre-review LangGraph state is stored in the SQL case. After a process restart, the service reconstructs the review checkpoint from that snapshot, reaches the interrupt, and resumes with the authenticated decision. The SQL transaction owns the final review commit. Intermediate specialist steps are pure and may be recomputed before a completed snapshot is saved. This recovery model avoids external side effects inside the workflow stages. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

Audit records contain the preceding hash, case ID, version, actor, action, time, and rationale. A trusted external anchor or immutable archival destination is required to detect a privileged rewrite of the entire chain. The hash chain currently supports consistency checks within the application ledger.

## 7. Kafka and the transactional outbox

The consumer disables automatic commits and automatic offset storage. It commits an offset only after a case has been committed or the malformed record has been durably quarantined. Database failures leave the offset uncommitted. Replaying the event is safe because SQL enforces identity and payload consistency. [Confluent Python client](https://docs.confluent.io/kafka-clients/python/current/overview.html)

Kafka and PostgreSQL do not share a transaction. The implementation therefore uses at-least-once delivery with idempotent persistence. A crash after SQL commit and before offset commit causes a replay. A crash after graph publication and before the outbox is marked complete causes repeated graph insertion. The graph URI and triples are deterministic, so set insertion produces the same graph content.

Projection failures leave a pending outbox entry with its attempt count and error type. The worker retries pending entries while polling. The current worker is a single straightforward consumer loop. A production deployment should separate projection workers when slow external calls compete with ingestion, implement retry backoff and dead-letter escalation, and monitor consumer lag and oldest pending projection age.

The Docker Compose stack is a local integration environment with one Kafka broker and one PostgreSQL instance. Production replication, backup, disaster recovery, topic ACLs, TLS, authentication, and capacity sizing depend on the target environment. Source producers must be trusted and authorized to publish each tenant's records.

## 8. Neptune projection

The adapter signs requests with AWS Signature Version 4 for the `neptune-db` service. It refreshes credentials through the AWS SDK provider chain. RDFLib serializes the triples, and hashed tenant and run identifiers construct the graph URI. User text is never interpolated into SPARQL syntax. The client uses an HTTPS endpoint, request timeouts, and disabled redirects. [AWS IAM authentication](https://docs.aws.amazon.com/neptune/latest/userguide/iam-auth-connecting.html)

Every case occupies a dedicated named graph. Queries in an institutional deployment must explicitly scope the graph and tenant. Neptune queries without graph scoping can consider the union of named graphs. The current investigation reads its validated in-memory case graph and asynchronously exports an archival RDF projection. Neptune-backed interactive retrieval requires integration testing against the actual VPC and IAM policy. [Neptune named graphs](https://docs.aws.amazon.com/neptune/latest/userguide/best-practices-sparql-query.html)

Local mode writes an atomic N-Triples file for each case. Adapter tests inspect the SigV4 request and graph scope. Live AWS provisioning and connectivity are outside the current verification record.

## 9. Authentication and API boundaries

Public routes accept only five configured investigation IDs and a fixed set of benchmark example IDs. Custom case content enters through `/api/v1/events`, which requires an OIDC token. The issuer, audience, JWKS endpoint, signature algorithm, expiry, issue time, subject, and tenant claims are validated. Roles are explicit lists containing `ingest`, `analyst`, or `reviewer` as applicable.

All read and review operations scope data using the tenant from the verified token. An ingestion tenant mismatch returns 403. Access to another tenant's case returns 404. The reviewer identity is never accepted from a form field. Tokens are kept in the browser session and are excluded from application logs.

| Route | Access | Purpose |
|---|---|---|
| `GET /api/health` | Public | Runtime and integration status |
| `GET /api/cases` | Public | Authored synthetic investigation cases |
| `POST /api/investigate` | Public fixed ID | Execute an investigation demonstration |
| `GET /api/model/evaluation` | Public | Dataset and measured model metrics |
| `POST /api/model/score` | Public fixed ID | Compute scores for a held-out example |
| `POST /api/v1/events` | Ingest role | Commit a validated investigation |
| `GET /api/v1/investigations/{id}` | Analyst role | Read a tenant-scoped saved case |
| `POST /api/v1/investigations/{id}/reviews` | Reviewer role | Record a versioned decision |
| `GET /api/v1/investigations/{id}/audit` | Analyst role | Inspect the recorded review history |

Private routes fail closed when authentication or database configuration is absent. Static file serving uses an allowlist. The Vercel configuration adds content-type, referrer, and content-security headers. Per-user rate limiting, enterprise identity integration, field redaction, key rotation procedures, retention enforcement, and a penetration test remain deployment work.

## 10. Data provenance and sampling

The model source is IBM AMLworld HI-Small, retrieved from the official Kaggle dataset endpoint linked by IBM. The source contains 5,078,345 transactions, including 5,177 synthetic laundering labels. Its SHA-256 is `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040`. The importer rejects a different checksum or schema. [IBM dataset repository](https://github.com/IBM/AML-Data)

A Bernoulli sample selects 5% of rows using seed 42 before using labels. It contains 253,980 transactions and 267 positive labels. This selection preserves an approximate natural class imbalance but removes transaction neighbors and can break motifs. Graph results therefore describe the sampled graph. The preparation command accepts a larger fraction for further experiments. The complete source CSV is excluded from the repository.

Account identifiers combine bank ID and account number. This avoids merging distinct accounts with identical local account numbers. The duplicate `Account` CSV headings are validated explicitly. Currency and payment-format vocabularies are fitted from the training period, and unseen values have an unknown category. Source timestamps lack an explicit zone, so benchmark ordering treats them as UTC.

IBM publishes the dataset under CDLA-Sharing-1.0, separately from the repository's software license. The exported inference examples contain a small derivative subset and retain the source attribution and sharing license. [Dataset license information](https://github.com/IBM/AML-Data/blob/main/README.md)

## 11. Temporal model experiment

| Partition | Transaction time | Graph visible to the model |
|---|---|---|
| Warmup | Before 4 September 2022 | Initial history only |
| Training | 4 to 7 September inclusive | History before 4 September |
| Validation | 8 September | History before 8 September |
| Test | 9 September onward | History before 9 September |

Equal-timestamp transactions stay together. A target-period edge is absent from that partition's graph. Validation and test features use prior transactions without their labels. The node universe is an indexing structure without learned ID embeddings. Unseen accounts have zero historical features. Standardization is fitted using training edges only.

The GraphSAGE comparator has two directed message-passing layers with separate incoming and outgoing aggregation and 16-dimensional hidden states. Initial node features describe historical incoming and outgoing counts and their availability. The edge head receives sender and receiver embeddings plus transaction features. Currency indicators accompany the log amount. A same-currency amount difference is computed only when the currency matches.

Training uses Adam with learning rate 0.003, weight decay 0.0001, gradient clipping at 5, and weighted binary cross-entropy. The positive weight is the square root of the negative-to-positive training ratio. Twenty epochs are evaluated on validation average precision. Epoch 12 is selected. The training set contains 82,989 transactions and 110 positives. The sampled node index contains 201,405 accounts.

Logistic regression and histogram gradient boosting receive the same transaction features plus historical sender and receiver degree features. Logistic regression uses balanced class weights. Gradient boosting uses 100 iterations, 15 maximum leaf nodes, learning rate 0.08, and L2 regularization of 1. Its internal random validation split is disabled. Each model's decision threshold is selected on validation F1. Held-out metrics are calculated afterward.

The JSON artifacts include model weights, thresholds, feature names, scaler values, dataset hashes, split boundaries, software versions, and measured metrics. The server computes logistic, tree, and GNN edge-head scores without loading pickle files. Tests compare the exported predictions with training-runtime reference predictions.

## 12. Measured results

The test partition contains 43,126 transactions, with 74 positives and a prevalence of 0.1716%. Values below come from the checked-in `artifacts/evaluation.json`.

| Model | Average precision | ROC AUC | Precision in top 1% | Recall in top 1% |
|---|---|---|---|---|
| Directed GraphSAGE | 0.0117 | 0.8466 | 0.46% | 2.70% |
| Logistic regression | 0.0179 | 0.9407 | 1.16% | 6.76% |
| Histogram gradient boosting | 0.0900 | 0.9608 | 6.71% | 39.19% |

Gradient boosting leads on validation average precision and on the recorded test comparison. The GNN underperforms both tabular baselines on test average precision. This result supports improving graph coverage, causal features, and evaluation before adding architectural complexity. The model scores remain uncalibrated. Brier scores and the validation-selected thresholds are available in the machine-readable report.

The result has substantial limits. It covers one sample, one seed, a small number of positives, and a single synthetic source. Confidence intervals, independent temporal windows, unsampled history, model calibration, cross-bank transfer, and operational analyst outcomes remain unmeasured. A high ROC AUC can coexist with a low proportion of relevant alerts under this class imbalance, so the UI reports review-budget precision and recall alongside it.

The inference cards deliberately include high-score examples, positive-label examples, and evenly spaced test examples. They illustrate behavior. Dataset-wide evaluation uses every row in the held-out partition. GNN embeddings on those cards were computed before the test cutoff, and the edge head runs on each API request.

## 13. Failure behavior

| Failure | Implemented response | Verification |
|---|---|---|
| Exact Kafka redelivery | Return existing committed case | Idempotency and integration checks |
| Changed payload under reused ID | Conflict | Regression test |
| Invalid account reference or time | Reject contract before workflow | Contract tests |
| Malformed Kafka event | Persist quarantine before offset acknowledgement | Worker boundary test |
| Database commit failure | Roll back event, case, audit, and outbox | Fault-injection test |
| Graph publication failure | Preserve pending projection for retry | Fault-injection test |
| Worker restart after SQL commit | Replay safely through event identity | Stack smoke test |
| Competing reviewer requests | One version update wins | Concurrent test |
| Review retry | Return saved response for identical request | Idempotency test |
| Expired or wrong-issuer JWT | Reject before business operation | Signed-token test |
| Other tenant requests a case | Return 404 | HTTP test |
| Model export changes numerics | Fail prediction-parity test | Inference artifact test |
| Live screening unavailable | Keep an explicit gap in the draft | Existing investigation test |

## 14. Deployment and CI

The Vercel deployment serves static assets and short FastAPI requests. Kafka consumption, a continuously available private graph connection, and model training run outside the request function. The public workbench is useful without cloud database credentials. Connecting its Pipeline tab to the private API requires the service configuration described in the README. [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)

The `CI` workflow runs linting, formatting checks, regression tests, and JavaScript syntax validation. A separate job starts PostgreSQL, Kafka, the API, and the worker in Docker Compose, publishes five events, verifies persisted cases and graph files, records an idempotent review, restarts the worker, and replays the events.

The benchmark workflow downloads the checksum-verified source, prepares the sample, trains all three models, and uploads the JSON artifacts. It runs on manual dispatch so ordinary code pushes avoid repeated dataset downloads. The deployment workflow reruns verification before using configured Vercel credentials. Its required GitHub secret and variables are documented explicitly. The current deployment can also be published through the connected Vercel account.

## 15. Operational acceptance before bank use

A bank deployment needs a named owner for source contracts, identity resolution, data retention, screening policy, model validation, incident response, and reviewer authorization. The workflow should be exercised against approved data with the actual tenant and identity configuration. Production targets for throughput, latency, recovery time, recovery point, and alert capacity need to be established with workload measurements.

Review objectives should include missing-evidence recall, false-positive workload, model drift by customer cohort and payment type, stale ownership and screening data, rejected-schema rate, Kafka lag, outbox delay, review conflicts, and audit completeness. Benchmark accuracy alone cannot establish operational suitability.

The most material extensions are full-history graph features, independent temporal evaluation, a live screening adapter with source-freshness controls, institution-approved ownership logic, persistent document evidence, and measured analyst evaluation. Those extensions should have explicit acceptance tests and a deployment-specific threat model.

## 16. Research register

The register covers 26 selected papers. Methods and experiment sections were inspected for AMLworld, Elliptic2, SynthAML, LaundroGraph, and collaborative AML. Other entries identify the abstract or primary-source description used. This is a targeted literature review with a separate FinGraph experiment. Published results are attributed to their own settings.

<!-- PAPER_REGISTER -->
