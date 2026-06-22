# FraudShield

Real-time Card-Not-Present fraud detection. Clean architecture: Repository + Strategy + Observer patterns. SQLite → PostgreSQL / Kafka in prod — no code changes.

Full MLOps loop automated: **drift detection → challenger training → AUC comparison → promotion → health check → auto-rollback**.

---

## Setup

```bash
cp .env.example .env          # set API_TOKEN before non-dev use

python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .              # required — makes fraud_api / fraudshield_core importable
```

---

## Quick Start

```bash
docker-compose up -d redis

python scripts/seed_data.py   # 90 days of synthetic transactions
python scripts/train_model.py # train XGBoost champion

cd dashboard ; npm install   # install dashboard deps once
honcho start                  # starts API + simulator + dashboard in one terminal
```

`honcho` reads the `Procfile` at the project root and runs all three processes together, colour-coding each line by service. Ctrl-C kills everything cleanly.

> **Simulator options** — to change the transaction rate, edit `Procfile` and append `--rate 1.0` (1/s) or `--rate 5.0` (1/5s) to the `simulator:` line.

| Service | URL |
|---|---|
| API + interactive docs | http://localhost:8000 / /docs |
| Dashboard | http://localhost:3000 |
| Prometheus metrics | http://localhost:8000/metrics |
| Health | http://localhost:8000/health |

---

## Full Stack (Prometheus + Grafana + Jaeger)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

Run both together — Docker for observability, honcho for the API:

```bash
# Terminal 1 — observability stack (background)
docker-compose up -d

# Terminal 2 — API + simulator + dashboard
honcho start
```

| Service | URL | Login |
|---|---|---|
| Dashboard | http://localhost:3000 | — |
| Grafana | http://localhost:3001 | admin / fraudshield |
| Prometheus | http://localhost:9090 | — |
| Jaeger (tracing) | http://localhost:16686 | — |

> Grafana is on **:3001** (not :3000) so it doesn't conflict with the dashboard dev server.
> Grafana shows "No data" until `honcho start` is running — Prometheus scrapes metrics from the live API.

Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` in `.env` to enable distributed tracing to Jaeger.

---

## API Reference

All endpoints except `/health*` and `/metrics` require `Authorization: Bearer <API_TOKEN>`.

Rate limit: **300 req/min per token** (configurable via `RATE_LIMIT_PER_SECOND`). Response headers `X-RateLimit-*` show remaining budget.

### Scoring

| Method | Endpoint | Notes |
|---|---|---|
| `POST` | `/v1/transactions/score` | Score one transaction; supports `dry_run`, `Idempotency-Key` header |
| `POST` | `/v1/transactions/score/batch` | Up to 100 transactions per call; per-item error isolation; 10 req/min |
| `POST` | `/v1/transactions/score/explain` | Dry-run score + SHAP reason codes inline (never persisted) |

### Transactions

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/v1/transactions` | List recent scores; filterable by `user_id`, `decision`, cursor-paginated |
| `GET` | `/v1/transactions/{id}` | Single transaction + score |
| `GET` | `/v1/transactions/{id}/explain` | Stored SHAP reason codes for a past transaction |

### Users

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/v1/users/{id}` | User profile |
| `GET` | `/v1/users/{id}/history` | Risk tier change audit trail (SCD Type-2) |
| `PATCH` | `/v1/users/{id}/risk-tier` | Analyst override; change is logged with caller IP |

### Model

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/v1/model/info` | Champion metadata |
| `GET` | `/v1/model/versions` | All versions in the local registry |
| `GET` | `/v1/model/features` | Feature names from the current champion |
| `GET` | `/v1/model/card` | Structured card: metrics, features, thresholds |
| `POST` | `/v1/model/reload` | Hot-reload champion from disk without restarting |
| `POST` | `/v1/model/promote/{version}` | Promote a registry version to champion |
| `POST` | `/v1/model/retrain` | Background retrain (202 Accepted); 409 if already running |

### Other

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/v1/drift/report` | PSI drift detection (live); auto-queues retrain when `DRIFT_AUTO_RETRAIN=true` |
| `GET` | `/v1/stream` | SSE push per scored transaction; auth via `?token=` |
| `POST` | `/v1/stream/token` | Exchange API token for 60s SSE token (browsers can't set headers on EventSource) |
| `POST` | `/v1/labels` | Submit fraud label (chargeback / human review) |
| `GET` | `/v1/dashboard/stats` | 24-hour scoring summary |
| `GET` | `/health` | Deep check — DB + Redis + model; returns `ok` or `degraded` |
| `GET` | `/health/live` | K8s liveness probe — 200 while process is alive |
| `GET` | `/health/ready` | K8s readiness probe — 503 if DB / Redis / model not ready |
| `GET` | `/metrics` | Prometheus scrape endpoint |

---

## Score a Transaction

```bash
curl -X POST http://localhost:8000/v1/transactions/score \
  -H "Authorization: Bearer dev_token_fraudshield_local_only" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_001",
    "user_id": "u_0001",
    "merchant_id": "m_crypto",
    "amount": 45000,
    "currency": "INR",
    "channel": "online",
    "ip_address": "185.220.101.5"
  }'
```

- `"dry_run": true` — score without persisting or appearing in Live Feed.
- `Idempotency-Key: <key>` header — same key always returns the cached response (24h Redis TTL).
- All amounts are normalised to INR before feature computation (`FX_USD`, `FX_EUR` in `.env`).

---

## Dashboard Pages

| Page | Route | What it shows |
|---|---|---|
| Live Feed | `/` | SSE push stream — every scored transaction in real time, pause/resume |
| Investigate | `/investigate` | Unified analyst workbench: user search, transaction table, SHAP codes, tier controls, audit trail |
| Score Explainer | `/explainer` | Submit any transaction, see probability + SHAP feature contributions (always dry-run) |
| Model Performance | `/model` | Champion metrics, version list, promote button, retrain trigger |
| Drift Monitor | `/drift` | PSI bar chart per feature, retrain recommendation, last-run timestamp |

---

## MLOps Loop

### Manual scripts

```bash
python scripts/check_drift.py                    # PSI drift report
python scripts/inject_drift.py                   # simulate Month-2 concept drift
python scripts/retrain_loop.py                   # drift → train challenger → compare AUC → promote
python scripts/retrain_loop.py --force true      # retrain even if drift is within safe range
python scripts/auto_rollback.py                  # post-promotion offline + online health check
python scripts/auto_rollback.py --dry-run        # evaluate without rolling back
```

`retrain_result.json` is written after each run with PSI, challenger vs champion AUC, and promotion result.

### GitHub Actions

| Workflow | Trigger | What it does |
|---|---|---|
| `test.yml` | Every push / PR | pytest + Redis integration tests |
| `retrain_loop.yml` | Daily 02:00 UTC + manual | Seed → train → compare → promote; uploads model registry artifact |
| `auto_rollback.yml` | After retrain + manual | Downloads artifact, runs offline + online health check, rolls back on failure |
| `deploy.yml` | Manual | Build + deploy to target environment |

### Auto-Retrain via Drift API

Set `DRIFT_AUTO_RETRAIN=true`. When `GET /v1/drift/report` returns `RETRAIN_REQUIRED`, a background retrain is automatically queued. Concurrent retrain requests (from drift or analyst) return 409.

### A/B Champion/Challenger

Set `CHALLENGER_MODEL_VERSION` and `CHALLENGER_TRAFFIC_PCT` in `.env`. The model poll thread (every 30s) auto-promotes the challenger when its AUC exceeds the champion's by ≥ `AB_PROMOTE_THRESHOLD` (default `0.01`).

---

## Auto-Rollback

Every promotion saves the previous champion to `PREVIOUS`. On health check failure the registry restores it.

| Check | Threshold | Notes |
|---|---|---|
| AUC-ROC (offline) | < 0.70 | Catastrophically weak model |
| Precision (offline) | < 0.60 | Too many false positives |
| Block rate deviation (online) | > 15 pp from training fraud_rate | Miscalibrated |
| SLA breach rate (online) | > 5% requests > 200 ms | Latency regression |

Exit codes: `0` = healthy, `2` = rollback triggered.

---

## PSI Drift Thresholds

| PSI | Status |
|---|---|
| < 0.10 | STABLE |
| 0.10 – 0.20 | MONITOR |
| > 0.20 | RETRAIN_REQUIRED |

---

## Prometheus Metrics

| Metric | Type | Description |
|---|---|---|
| `fraudshield_decisions_total` | Counter | Per `decision` + `strategy` |
| `fraudshield_score` | Histogram | Raw fraud score distribution |
| `fraudshield_scoring_latency_seconds` | Histogram | End-to-end scoring time |
| `fraudshield_sla_breaches_total` | Counter | Decisions > 200 ms |
| `fraudshield_model_info` | Gauge | Currently loaded version + strategy |
| `fraudshield_psi_score` | Gauge | Per-feature PSI (Drift Monitor) |
| `fraudshield_score_by_variant_bucket` | Histogram | A/B score distribution per variant |

---

## Running Tests

```bash
pytest tests/ -v --tb=short                   # unit + integration (no Redis needed)
docker-compose up -d redis
pytest tests/test_redis_features.py -v        # Redis velocity + idempotency tests
cd dashboard && npm test                      # frontend component tests (jsdom)
locust -f tests/locustfile.py --host http://localhost:8000   # load test
```

Test files: `test_api.py`, `test_scoring.py`, `test_repository.py`, `test_redis_features.py`, `test_scorer_properties.py`, `test_contract.py`.

---

## Key Config (`.env`)

| Variable | Default | Effect |
|---|---|---|
| `API_TOKEN` | `dev_token_...` | Bearer token for all API calls |
| `FRAUD_THRESHOLD` | `0.85` | Score ≥ this → block |
| `REVIEW_THRESHOLD` | `0.50` | Score ≥ this → review |
| `RATE_LIMIT_PER_SECOND` | `100` | API rate limit (300/min per token) |
| `DRIFT_AUTO_RETRAIN` | `false` | Auto-queue retrain on drift |
| `AB_PROMOTE_THRESHOLD` | `0.01` | Min AUC delta to auto-promote challenger |
| `CHALLENGER_MODEL_VERSION` | `` | Enable A/B routing |
| `CHALLENGER_TRAFFIC_PCT` | `0.0` | Fraction of traffic to challenger |
| `WEBHOOK_URL` | `` | POST target for fraud decisions |
| `WEBHOOK_EVENTS` | `block` | Which decisions trigger webhook (`block,review,allow`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `` | Enable OTel tracing (Jaeger) |
| `PSI_THRESHOLD` | `0.20` | PSI above this = RETRAIN_REQUIRED |
| `DRIFT_CHECK_WINDOW_DAYS` | `7` | Recency window for PSI |
| `MODEL_REGISTRY_PATH` | `local_store/model_registry` | Where versioned models live |
| `MLFLOW_TRACKING_URI` | `local_store/mlruns` | MLflow experiment tracking |
| `USE_PU_LEARNING` | `false` | PU Learning for training with unlabeled negatives |

---

## Production Scaling (one-line swaps in `main.py`)

| Component | MVP | Production swap |
|---|---|---|
| Database | SQLite | `PostgreSQLUserRepository` |
| Event bus | In-memory | `RedisStreamsPublisher` |
| Model registry | Local files | `MLflowRegistry` |
| Feature store | Redis dict | Feast / Tecton |
| Scheduler | GitHub Actions | Airflow DAG |
| Observability | Prometheus + Grafana | Same — no change needed |
