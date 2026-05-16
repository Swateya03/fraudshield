# FraudShield MVP
### Real-Time CNP Fraud Detection System

A production-ready MVP for Card-Not-Present fraud detection.
Built with clean architecture: Repository + Strategy + Observer patterns.
Designed to scale from SQLite on your laptop to PostgreSQL + Kafka in production — **zero rewrites**.

The full MLOps loop is automated: **drift detection → challenger training → champion/challenger evaluation → promotion → health check → auto-rollback**.

---

## Table of Contents

1. [Development Setup](#1-development-setup)
2. [Quick Start](#2-quick-start)
3. [React Dashboard + Live Feed](#3-react-dashboard--live-feed)
4. [Full Stack with Observability](#4-full-stack-with-observability)
5. [Score a Transaction](#5-score-a-transaction)
6. [MLOps Loop — Manual](#6-mlops-loop--manual)
7. [MLOps Loop — Automated (CI)](#7-mlops-loop--automated-ci)
8. [Auto-Rollback](#8-auto-rollback)
9. [Drift Detection](#9-drift-detection)
10. [Grafana Dashboard](#10-grafana-dashboard)
11. [Prometheus Metrics](#11-prometheus-metrics)
12. [Running Tests](#12-running-tests)
13. [API Reference](#13-api-reference)
14. [Architecture](#14-architecture)
15. [Project Structure](#15-project-structure)
16. [Production Scaling](#16-production-scaling)

---

## 1. Development Setup

> Required once per clone.

### Environment variables

```bash
cp .env.example .env
# Edit .env — set API_TOKEN to a strong secret for non-dev deployments.
# All other defaults work out of the box locally.
```

### Python packages

Python imports (`fraudshield_core`, `fraud_api`, `ml_pipeline`) come from this repo. Install in **editable** mode so scripts, tests, and the API all resolve packages identically:

```bash
cd /path/to/fraudshield-mvp

python -m venv .venv

# Activate
# Windows:     .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

> Without `pip install -e .` you will see `ModuleNotFoundError` for project packages. CI runs it automatically.

---

## 2. Quick Start

Minimum viable run — only Redis needed as an external service.

```bash
# Terminal 1 — Infrastructure
docker-compose up -d redis

# Seed the database (90 days of synthetic transactions)
python scripts/seed_data.py

# Train the initial XGBoost champion model
python scripts/train_model.py

# Terminal 1 — API
python -m fraud_api.main

# Terminal 2 — Dashboard
cd dashboard && npm install && npm run dev

# Terminal 3 — Live transaction simulator (makes the Live Feed look real)
python scripts/simulate_live.py
```

| Endpoint | URL |
|---|---|
| API | http://localhost:8000 |
| Interactive docs | http://localhost:8000/docs |
| React dashboard | http://localhost:5173 |
| Prometheus metrics | http://localhost:8000/metrics |
| Health check | http://localhost:8000/health |

---

## 3. React Dashboard + Live Feed

The `dashboard/` directory is a React + Vite single-page app providing a real-time fraud analyst interface.

### Install

```bash
cd dashboard
npm install
```

### Run

**Full stack (recommended):**

```bash
# Terminal 1
python -m fraud_api.main

# Terminal 2
cd dashboard && npm run dev

# Terminal 3 — continuous transaction stream
python scripts/simulate_live.py
```

The Live Feed connects via **Server-Sent Events** (SSE) — every scored transaction appears instantly with no polling delay. The connection indicator shows **● Live** (green) when the SSE stream is active.

**Without the Python API** (mock data, no setup required):

```bash
npm run dev:stack
```

**Production build:**

```bash
npm run build    # outputs to dashboard/dist/
npm run preview  # serve the production bundle locally
```

### Simulator options

```bash
python scripts/simulate_live.py             # ~1 transaction every 2.5 seconds (default)
python scripts/simulate_live.py --rate 1.0  # faster — 1 transaction/second
python scripts/simulate_live.py --rate 5.0  # slower — 1 transaction/5 seconds
```

The simulator generates a realistic mix: **80% legitimate**, **12% suspicious**, **8% fraud** — across 15 users, 10 merchants, all 5 payment channels, and 3 currencies (INR/USD/EUR).

### Dashboard pages

| Page | What it shows |
|---|---|
| Live Feed | Real-time transaction stream via SSE — instant push, no polling |
| Explorer | Searchable + filterable transaction history with SHAP reason codes |
| Risk Manager | User risk tier overrides and KYC status |
| Model Performance | Champion model metrics, version history, drift report |

> **Score Explainer** submissions use `dry_run: true` — they return the full fraud probability and SHAP breakdown but are **never saved to the database** and **never appear in the Live Feed**. This keeps the feed clean for real operational transactions only.

---

## 4. Full Stack with Observability

Start every service — Redis, Postgres, Prometheus, and Grafana — with one command:

```bash
docker-compose up
```

| Service | URL | Credentials |
|---|---|---|
| FraudShield API | http://localhost:8000 | — |
| React Dashboard | http://localhost:5173 | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / fraudshield |

Grafana auto-provisions the **FraudShield — Model Observability** dashboard on first boot. No manual setup required.

```bash
docker-compose up -d          # background
docker-compose logs -f        # stream logs
docker-compose down           # stop, keep data
docker-compose down -v        # stop + wipe all volumes
```

---

## 5. Score a Transaction

```bash
curl -X POST http://localhost:8000/v1/transactions/score \
  -H "Authorization: Bearer dev_token_fraudshield_local_only" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_demo_001",
    "user_id": "u_0001",
    "merchant_id": "m_crypto",
    "amount": 45000.00,
    "currency": "INR",
    "channel": "online",
    "ip_address": "185.220.101.5"
  }'
```

Expected response:

```json
{
  "transaction_id": "txn_demo_001",
  "fraud_probability": 0.89,
  "decision": "block",
  "reason_codes": [
    {"code": "high_risk_ip",       "contribution": 2.497},
    {"code": "channel_risk",       "contribution": 1.356},
    {"code": "high_risk_merchant", "contribution": 0.837}
  ],
  "model_version": "v1.0.0",
  "latency_ms": 18,
  "request_id": "req_abc123"
}
```

### Currency normalisation

All amount-based features (`amount_ratio`, `amount_zscore`, `log_amount`) are computed on the **INR-equivalent** of the transaction amount before scoring. Exchange rates are configured in `.env`:

```bash
FX_USD=83.0   # 1 USD = 83 INR
FX_EUR=90.0   # 1 EUR = 90 INR
FX_GBP=105.0
```

A USD $3,000 transaction is treated as ₹2,49,000 — a 498× amount spike for a user whose average transaction is ₹500 — and scores significantly higher than an INR ₹3,000 transaction. This makes foreign-currency fraud visible without retraining the model.

### Dry run (no persistence)

Add `"dry_run": true` to get the full score + SHAP breakdown without writing anything to the database or the Live Feed:

```bash
curl -X POST http://localhost:8000/v1/transactions/score \
  -H "Authorization: Bearer dev_token_fraudshield_local_only" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_test_001",
    "user_id": "u_0001",
    "merchant_id": "m_crypto",
    "amount": 45000,
    "currency": "USD",
    "channel": "online",
    "dry_run": true
  }'
```

### Idempotent retries

Include an `Idempotency-Key` header — the same key always returns the same cached response (24-hour TTL in Redis):

```bash
curl -X POST http://localhost:8000/v1/transactions/score \
  -H "Authorization: Bearer dev_token_fraudshield_local_only" \
  -H "Idempotency-Key: my-unique-key-123" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

---

## 6. MLOps Loop — Manual

```bash
# Step 1 — Check feature drift (PSI across all monitored features)
python scripts/check_drift.py

# Step 2 — (Optional) Inject simulated concept drift to trigger retraining
python scripts/inject_drift.py

# Step 3 — Full retrain loop: drift → challenger → champion/challenger → promote
python scripts/retrain_loop.py

# Force retrain even when drift is within safe range
python scripts/retrain_loop.py --force true

# Step 4 — Post-promotion health check
python scripts/auto_rollback.py

# Dry-run: evaluate without rolling back
python scripts/auto_rollback.py --dry-run
```

`retrain_result.json` is written after every run:

```json
{
  "run_at": "2026-05-16T10:00:00",
  "recommendation": "RETRAIN_REQUIRED",
  "max_psi": 0.32,
  "retrain_triggered": true,
  "champion_version": "v1.0.0",
  "champion_auc": 0.9201,
  "challenger_version": "v1.1.0",
  "challenger_auc": 0.9344,
  "promoted": true,
  "promotion_reason": "auc_delta=+0.0143"
}
```

---

## 7. MLOps Loop — Automated (CI)

Two GitHub Actions workflows automate the full loop:

### Retrain Loop (`retrain_loop.yml`)

Runs **daily at 02:00 UTC**. Steps:
1. Seed database (90 days of synthetic transactions)
2. Train a baseline champion if none exists
3. Inject concept drift (simulates Month 2 attacker pattern shift)
4. Run `retrain_loop.py` — drift → challenger → champion/challenger AUC comparison → promotion
5. Write a job summary with the results table
6. Upload `local_store/model_registry/` as a 30-day artifact

**Manual trigger with force flag:**

Go to **Actions → Retrain Loop → Run workflow** and set `force_retrain = true`.

### Auto-Rollback (`auto_rollback.yml`)

Triggered automatically after every successful retrain loop. Also runs on `workflow_dispatch`.

Steps:
1. Downloads the model registry artifact from the retrain loop
2. Runs `auto_rollback.py` (offline check always; online check when Prometheus is reachable)
3. Writes a job summary with health check results and rollback decision

**Manual trigger with dry-run:**

Go to **Actions → Auto-Rollback Health Check → Run workflow**, set `dry_run = true`.

---

## 8. Auto-Rollback

### Offline check (always runs)

| Metric | Minimum | What it catches |
|---|---|---|
| AUC-ROC | 0.70 | Catastrophically weak model |
| Precision | 0.60 | Too many false positives |

### Online check (when Prometheus is reachable)

| Signal | Threshold | What it catches |
|---|---|---|
| Block rate deviation | > 15 pp from training `fraud_rate` | Miscalibrated model |
| SLA breach rate | > 5% of decisions > 200 ms | Latency regression |

### Rollback mechanism

Every promotion saves the previous champion to `PREVIOUS`. On health check failure, `registry.rollback()` restores it.

```bash
python scripts/auto_rollback.py --prometheus-url http://localhost:9090 --window-minutes 30
python scripts/auto_rollback.py --dry-run
python scripts/auto_rollback.py --output health_check.json
```

Exit codes: `0` = healthy, `1` = fatal error, `2` = rollback triggered.

---

## 9. Drift Detection

FraudShield uses **Population Stability Index (PSI)** to detect feature distribution shift between a 60-day baseline window and the most recent 7 days.

```bash
python scripts/check_drift.py
```

Output:

```
Feature              PSI       Status
─────────────────────────────────────
velocity_1h          0.031     STABLE
amount_ratio         0.048     STABLE
ip_fraud_history     0.241     RETRAIN_REQUIRED  ←
device_trust_score   0.198     MONITOR
...

Max PSI: 0.241 → RETRAIN_REQUIRED
```

| PSI | Meaning |
|---|---|
| < 0.10 | STABLE — no action needed |
| 0.10 – 0.20 | MONITOR — watch next window |
| > 0.20 | RETRAIN_REQUIRED — trigger challenger |

```bash
python scripts/inject_drift.py   # simulate Month 2 fraud pattern
```

---

## 10. Grafana Dashboard

Open **http://localhost:3000** → login with `admin / fraudshield` → the **FraudShield — Model Observability** dashboard loads automatically.

**Top row — stat cards:**

| Panel | What it shows |
|---|---|
| Decisions / sec | Live throughput |
| Block Rate (last 5m) | % of transactions blocked |
| p95 Latency | 95th percentile scoring time |
| SLA Breaches (last 1h) | Count of requests > 200 ms |

**Middle rows — time series:**

| Panel | What it shows |
|---|---|
| Decision Rate — Allow / Review / Block | Per-decision throughput over time |
| Scoring Latency — p50 / p95 / p99 | Latency percentiles over time |
| Fraud Score Distribution | Bucket distribution of raw scores |
| Decision Rate by Strategy | Throughput split by rule-based vs XGBoost |

**Bottom rows — model health:**

| Panel | What it shows |
|---|---|
| Active Model | Current champion version + strategy |
| Rollback Target | Previous champion (rollback destination) |
| Block Rate % Drift | Live block rate vs rollback threshold (+15 pp) |
| SLA Breach Rate | Breach rate vs rollback threshold (5%) |

Refreshes every **10 seconds**, defaults to the **last 1 hour** window.

---

## 11. Prometheus Metrics

Exposed at `/metrics`. Scraped every 15 seconds by the Prometheus container.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `fraudshield_decisions_total` | Counter | `decision`, `strategy` | Total scoring decisions |
| `fraudshield_score` | Histogram | — | Raw fraud score distribution |
| `fraudshield_scoring_latency_seconds` | Histogram | — | End-to-end scoring time |
| `fraudshield_sla_breaches_total` | Counter | — | Decisions > 200 ms |
| `fraudshield_model_info` | Gauge | `version`, `strategy` | Currently loaded model |

```promql
# Live block rate %
100 * sum(rate(fraudshield_decisions_total{decision="block"}[5m]))
    / sum(rate(fraudshield_decisions_total[5m]))

# p95 latency
histogram_quantile(0.95,
  sum(rate(fraudshield_scoring_latency_seconds_bucket[5m])) by (le))
```

---

## 12. Running Tests

```bash
# Unit + integration (no Redis required)
pytest tests/ -v --tb=short

# Redis integration tests (velocity, feature store, idempotency)
docker-compose up -d redis
pytest tests/test_redis_features.py -v
```

Redis tests auto-skip when Redis is unavailable. In CI they run in the `redis-integration` job with a `redis:7-alpine` service container.

---

## 13. API Reference

All endpoints except `/health` and `/metrics` require:

```
Authorization: Bearer <API_TOKEN>
```

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — returns strategy + version |
| `GET` | `/metrics` | Prometheus scrape endpoint |
| `POST` | `/v1/transactions/score` | Score a transaction for CNP fraud |
| `GET` | `/v1/stream` | SSE stream — push event per scored transaction |
| `GET` | `/v1/transactions` | List recent scored transactions |
| `GET` | `/v1/transactions/{id}` | Fetch a single transaction + score |
| `GET` | `/v1/users/{id}` | Fetch user profile |
| `PATCH` | `/v1/users/{id}/risk-tier` | Analyst overrides risk tier |
| `POST` | `/v1/labels` | Submit a fraud label (chargeback / review) |
| `GET` | `/v1/model/info` | Current champion model metadata |
| `GET` | `/v1/model/versions` | All versions in the registry |
| `POST` | `/v1/model/reload` | Hot-reload champion without restarting |
| `GET` | `/v1/drift/report` | Run PSI drift report (live) |
| `GET` | `/v1/dashboard/stats` | 24-hour scoring summary |

> `GET /v1/stream` authenticates via `?token=` query parameter (browsers cannot set headers on EventSource connections).

Interactive docs: **http://localhost:8000/docs**

---

## 14. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                FraudShield — Full Architecture                 │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Payment Gateway / Simulator                                   │
│       │ POST /v1/transactions/score                            │
│       ▼                                                        │
│  [fraud_api/main.py]   ←── Bearer auth + idempotency key       │
│       │ authenticate → validate → process → emit metrics       │
│       ▼                                                        │
│  [FraudService]                                                │
│       ├── UserRepository.get_by_id()     → SQLite / Postgres   │
│       ├── FeatureBuilder.build()         → Redis + DB          │
│       │     └─ currency normalisation   → amount → INR equiv   │
│       ├── FraudScorer.score()            → Strategy (below)    │
│       └── EventPublisher.publish()       → Observers           │
│                                                                │
│  [Scoring Strategy — swap one line in main.py]                 │
│       ├── RuleBasedStrategy   (fallback, no model needed)      │
│       └── XGBoostStrategy     (calibrated, SHAP reason codes)  │
│                                                                │
│  [SSEBroadcaster] ←── broadcasts every non-dry-run score       │
│       └─ GET /v1/stream → LiveFeed (instant push, no polling)  │
│                                                                │
│  [Redis]   velocity counters (1h/6h/24h) + offline features   │
│            + idempotency cache (24h TTL)                       │
│  [SQLite]  users, transactions, scores, labels, history        │
│                                                                │
│  [Prometheus] ← scrapes /metrics every 15s                     │
│  [Grafana]    ← auto-provisioned 12-panel dashboard            │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│                    Automated MLOps Loop                        │
│                                                                │
│  GitHub Actions — daily cron @ 02:00 UTC                       │
│                                                                │
│  1. check_drift.py      PSI > 0.20 → RETRAIN_REQUIRED         │
│       ↓                                                        │
│  2. retrain_loop.py     Train challenger model                 │
│       ↓                                                        │
│  3. Champion/Challenger  Compare AUC (tolerance −0.005)        │
│       ↓ challenger wins                                        │
│  4. registry.promote()   Champion flipped, PREVIOUS saved      │
│       ↓                                                        │
│  5. auto_rollback.py    Offline + online health checks         │
│       ↓ failure                                                │
│  6. registry.rollback()  Restore previous champion             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 15. Project Structure

```
fraudshield-mvp/
│
├── fraud_api/                  ← Core scoring API (FastAPI)
│   ├── main.py                 ← Wire-up: DI, routes, /metrics mount
│   ├── metrics.py              ← Prometheus counters/histograms
│   ├── repository/             ← Storage abstraction (Repository pattern)
│   │   ├── base.py             ← Abstract interfaces
│   │   ├── sqlite_repo.py      ← SQLite implementations
│   │   └── inmemory_repo.py    ← In-memory (tests / dev)
│   ├── scoring/                ← ML scoring pipeline (Strategy pattern)
│   │   ├── scorer.py           ← FraudScorer (strategy-agnostic)
│   │   ├── feature_builder.py  ← Builds FeatureVector (+ currency normalisation)
│   │   └── strategies/
│   │       ├── rule_based.py   ← Deterministic rules + SHAP-style codes
│   │       └── xgboost_strategy.py ← Calibrated XGBoost + SHAP
│   ├── events/                 ← Observer pattern
│   │   ├── publisher.py        ← InMemoryPublisher (→ RedisStreams in prod)
│   │   ├── observers.py        ← audit_log, alert, risk_tier_updater
│   │   └── sse_broadcaster.py  ← In-process SSE pub/sub for Live Feed
│   └── services/
│       └── fraud_service.py    ← Orchestrates repo + scorer + publisher
│
├── ml_pipeline/                ← MLOps components
│   ├── data/
│   │   └── simulator.py        ← Archetype-based fraud simulator
│   ├── features/
│   │   ├── feature_store.py    ← Offline feature computation
│   │   └── precompute.py       ← Batch feature precomputation
│   ├── training/
│   │   ├── dataset.py          ← build_training_dataset (GroupShuffleSplit)
│   │   ├── train.py            ← XGBoost + Platt calibration + SHAP
│   │   ├── pu_learning.py      ← PU Learning (positive-unlabeled)
│   │   └── registry.py         ← LocalFileRegistry (champion/challenger)
│   └── monitoring/
│       └── drift.py            ← PSI drift detection
│
├── fraudshield_core/           ← Domain layer (no framework deps)
│   ├── models.py               ← All domain models + CHANNEL_RISK mapping
│   ├── config.py               ← Env-var config (dotenv) + exchange rates
│   ├── db.py                   ← SQLAlchemy engine + table creation
│   └── redis_client.py         ← Redis connection singleton
│
├── scripts/                    ← CLI tools
│   ├── seed_data.py            ← Populate DB with 90 days of synthetic data
│   ├── train_model.py          ← Train + register champion
│   ├── simulate_live.py        ← Continuous live transaction stream (15 users, 10 merchants)
│   ├── score_transaction.py    ← Interactive terminal scorer + labeler
│   ├── check_drift.py          ← PSI drift report
│   ├── inject_drift.py         ← Simulate concept drift (Month 2 fraud)
│   ├── retrain_loop.py         ← Drift → challenger → champion/challenger → promote
│   └── auto_rollback.py        ← Post-promotion health check + rollback
│
├── dashboard/                  ← React + Vite single-page app
│   ├── src/
│   │   ├── App.jsx             ← Router + layout
│   │   ├── pages/              ← LiveFeed (SSE), Explorer, RiskManager, ModelPerformance
│   │   ├── components/         ← Shared UI components
│   │   ├── api/client.js       ← API client (points to :8000)
│   │   └── lib/utils.js        ← fmtAmount(n, currency), fmtTime, scoreColor …
│   ├── dev-api.mjs             ← Lightweight Node mock API for frontend-only dev
│   └── package.json            ← npm scripts: dev, dev:stack, build, preview
│
├── monitoring/                 ← Observability config
│   ├── prometheus.yml          ← Scrape config (15s)
│   └── grafana/
│       └── dashboards/
│           └── fraudshield.json ← 12-panel Model Observability dashboard
│
├── .github/workflows/
│   ├── test.yml                ← pytest + Redis integration on every push/PR
│   ├── retrain_loop.yml        ← Daily cron drift → retrain → promote
│   └── auto_rollback.yml       ← Post-promotion health check → rollback
│
├── tests/
│   ├── test_api.py             ← FastAPI route tests (httpx)
│   ├── test_scoring.py         ← Scoring strategy + rule tests
│   ├── test_repository.py      ← Repository layer tests
│   └── test_redis_features.py  ← Redis integration tests
│
├── docker-compose.yml          ← Redis, Postgres, Prometheus, Grafana
├── requirements.txt
├── setup.py
├── .env.example                ← Environment template — copy to .env before running
└── local_store/                ← Runtime artifacts (contents gitignored, dir tracked)
    ├── fraud.db                ← SQLite database
    ├── model_registry/         ← Versioned model artifacts
    │   ├── v1.0.0/
    │   │   ├── model.pkl
    │   │   └── metadata.json
    │   ├── CURRENT             ← Points to champion version
    │   └── PREVIOUS            ← Rollback target
    └── mlruns/                 ← MLflow experiment tracking
```

---

## 16. Production Scaling

The architecture is designed so each component is a one-line swap in `fraud_api/main.py`. Business logic — `FraudService`, `FraudScorer`, all domain models — never changes.

| Component | MVP (now) | Production swap | Where to change |
|---|---|---|---|
| Database | SQLite | PostgreSQL | `main.py`: swap `SQLiteUserRepository` → `PostgreSQLUserRepository` |
| Event bus | In-memory dict | Kafka / Redis Streams | `main.py`: swap `InMemoryPublisher` → `RedisStreamsPublisher` |
| SSE stream | In-process queue | Redis Pub/Sub (multi-instance) | `sse_broadcaster.py`: swap `asyncio.Queue` → Redis channel |
| Scoring strategy | XGBoost local | Remote model server | `main.py`: swap `XGBoostStrategy` → `RemoteModelStrategy` |
| Model registry | Local files | MLflow server | `registry.py`: swap `LocalFileRegistry` → `MLflowRegistry` |
| Feature store | Redis dict | Feast / Tecton | `feature_builder.py`: swap Redis lookups → Feast SDK calls |
| Exchange rates | `.env` constants | Central FX API | `config.py`: fetch live rates on startup |
| Scheduler | GitHub Actions cron | Apache Airflow | Add `dags/retrain_dag.py` |
| Observability | Prometheus + Grafana | Same — already production-grade | No change needed |
