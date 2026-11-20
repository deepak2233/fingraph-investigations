# FinGraph runbook

Start with the [README](../README.md#run-locally) for the workbench and Docker commands. The Docker stack uses local development credentials and bound localhost ports.

## Inspect recovery

On a clean local stack, start intake with the projector offline. The smoke test expects exactly five fixtures.

```bash
docker compose up --build -d api worker
docker compose run --rm worker python -m pipeline.cli publish
docker compose exec -T worker python -m scripts.verify_stack --intake-only --check-offsets
docker compose up --build -d projector
docker compose exec -T worker python -m scripts.verify_stack
docker compose restart worker
docker compose run --rm worker python -m pipeline.cli publish
docker compose exec -T worker python -m scripts.verify_stack --check-offsets
```

For standalone deployments, run `python -m pipeline.worker` for Kafka intake and `python -m pipeline.projector` under a separate supervisor. Both processes use `DATABASE_URL`. Existing databases use the same outbox schema. Set `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, and optional `KAFKA_CLIENT_CONFIG` on intake. Set `GRAPH_EXPORT_DIR` on the projector for file outputs. Configure `NEPTUNE_ENDPOINT`, `AWS_REGION`, and AWS SDK credentials on the projector to use SigV4 transport. The Neptune transport tests use a mock endpoint. [Configuration example](../.env.example)

For the private HTTP API, configure `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL`. Tokens need signed RS256 claims `sub`, `tenant_id`, `iat`, `exp`, and `roles`. Roles are `ingest`, `analyst`, and `reviewer`. The case API is described in the [architecture](ARCHITECTURE.md#9-authentication-and-api-boundaries) and local FastAPI docs at `/docs`.

## Replay a ledger snapshot without Kafka

```bash
pip install -r requirements-services.txt
mkdir -p var
python -m pipeline.candidates --input data/ledger_snapshot.json --output var/events.jsonl
export DATABASE_URL=sqlite:///var/fingraph.db
python -m pipeline.cli init-db
python -m pipeline.cli replay --file var/events.jsonl
```

## Reproduce the benchmark

```bash
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt
mkdir -p data/raw
curl --fail --location --retry 3 \
  'https://www.kaggle.com/api/v1/datasets/download/ealtman2019/ibm-transactions-for-anti-money-laundering-aml/HI-Small_Trans.csv' \
  -o data/raw/HI-Small_Trans.csv
python -m ml.prepare --input data/raw/HI-Small_Trans.csv
python -m ml.train --epochs 20
python -m scripts.export_workbench
```

Install `requirements-dev.txt` in the same environment for the exporter and tests. The optional `train.yml` workflow runs CPU training and uploads the JSON artifacts. [Data and license](DATA_LICENSE.md)

## Check a release

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.export_workbench --check
python -m scripts.smoke_public https://fingraph-investigations.vercel.app
```

Regenerate the checked-in static assets with `python -m scripts.export_workbench` after editing fixtures, ontology, guides or model outputs. The optional [deploy workflow](../.github/workflows/deploy.yml) expects `VERCEL_TOKEN` as a secret and `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID` as variables. `PUBLIC_DEMO_URL` overrides the production alias used for smoke checks. [Verification record](VERIFICATION.md)
