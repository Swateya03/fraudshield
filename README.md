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
3. [Full Stack with Observability](#3-full-stack-with-observability)
4. [Score a Transaction](#4-score-a-transaction)
5. [MLOps Loop — Manual](#5-mlops-loop--manual)
6. [MLOps Loop — Automated (CI)](#6-mlops-loop--automated-ci)
7. [Auto-Rollback](#7-auto-rollback)
8. [Drift Detection](#8-drift-detection)
9. [Grafana Dashboard](#9-grafana-dashboard)
10. [Prometheus Metrics](#10-prometheus-metrics)
11. [Running Tests](#11-running-tests)
12. [API Reference](#12-api-reference)
13. [Architecture](#13-architecture)
14. [Project Structure](#14-project-structure)
15. [Production Scaling](#15-production-scaling)

---

## 1. Development Setup

> Required once per clone.

Python imports (`fraudshield_core`, `fraud_api`, `ml_pipeline`) come from this repo. Install in **editable** mode so scripts, tests, and the API all resolve packages identically:

```bash
cd /path/to/fraudshield_mvp

python -m venv .venv

# Activate
# Windows:    .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

> Without `pip install -e .` you will see `ModuleNotFoundError` for project packages. CI runs it automatically.

---

## 2. Quick Start

Minimum viable run — only Redis needed as an external service.

```bash
# 1. Start Redis
docker-compose up -d redis

# 2. Create runtime directories (gitignored; SQLite needs these)
mkdir -p local_store/model_registry local_store/mlruns

# 3. Seed the database with 90 days of synthetic transactions
python scripts/seed_data.py

# 4. Train the initial XGBoost champion model
python scripts/train_model.py

# 5. Start the API
python fraud_api/main.py
```

| Endpoint | URL |
|---|---|
| API | http://localhost:8000 |
| Interactive docs | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |
| Health check | http://localhost:8000/health |

---

## 3. Full Stack with Observability

Start every service — Redis, Postgres, Prometheus, and Grafana — with one command:

```bash
docker-compose up
```

| Service | URL | Credentials |
|---|---|---|
| FraudShield API | http://localhost:8000 | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / fraudshield |

Grafana auto-provisions the **FraudShield — Model Observability** dashboard on first boot. No manual setup required.

To run services in the background:

```bash
docker-compose up -d
docker-compose logs -f  # stream logs
```

To stop everything and keep data:

```bash
docker-compose down
```

To reset all volumes (clears Prometheus and Grafana data):

```bash
docker-compose down -v
```

---

## 4. Score a Transaction

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
  "fraud_probability": 0.94,
  "decision": "block",
  "reason_codes": [
    {"code": "high_risk_ip",       "contribution": 0.40},
    {"code": "amount_spike",       "contribution": 0.25},
    {"code": "high_risk_merchant", "contribution": 0.20},
    {"code": "new_device",         "contribution": 0.30}
  ],
  "model_version": "v1.0.0",
  "latency_ms": 47,
  "request_id": "req_abc123"
}
```

For idempotent retries, include an `Idempotency-Key` header — the same key always returns the same cached response:

```bash
curl -X POST http://localhost:8000/v1/transactions/score \
  -H "Authorization: Bearer dev_token_fraudshield_local_only" \
  -H "Idempotency-Key: my-unique-key-123" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

---

## 5. MLOps Loop — Manual

Run the loop step by step from the terminal:

```bash
# Step 1 — Check feature drift (PSI across all monitored features)
python scripts/check_drift.py

# Step 2 — (Optional) Inject a simulated concept drift to trigger retraining
python scripts/inject_drift.py

# Step 3 — Run the full retrain loop manually
#   Checks drift → trains challenger → compares AUC → promotes if challenger wins
python scripts/retrain_loop.py

# Force a retrain even if drift is within safe range
python scripts/retrain_loop.py --force true

# Save the result to a custom path
python scripts/retrain_loop.py --force true --output my_result.json

# Step 4 — After retraining, run the health check
python scripts/auto_rollback.py

# Dry-run: evaluate without actually rolling back
python scripts/auto_rollback.py --dry-run
```

`retrain_result.json` is written after every run with a full summary:

```json
{
  "run_at": "2026-05-06T10:00:00",
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

## 6. MLOps Loop — Automated (CI)

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

Go to **Actions → Retrain Loop → Run workflow** and set `force_retrain = true` to retrain even when drift is within safe range.

### Auto-Rollback (`auto_rollback.yml`)

Triggered automatically after every successful retrain loop. Also runs on `workflow_dispatch`.

Steps:
1. Downloads the model registry artifact from the retrain loop
2. Runs `auto_rollback.py` (offline check always; online check when Prometheus is reachable)
3. Writes a job summary with health check results and rollback decision

**Manual trigger with dry-run:**

Go to **Actions → Auto-Rollback Health Check → Run workflow**, set `dry_run = true` to inspect without promoting.

---

## 7. Auto-Rollback

The auto-rollback system has two layers of protection:

### Offline check (always runs)

Validates the champion's training metrics against minimum floors. Triggers rollback immediately if either fails.

| Metric | Minimum | What it catches |
|---|---|---|
| AUC-ROC | 0.70 | Catastrophically weak model |
| Precision | 0.60 | Too many false positives |

### Online check (when Prometheus is reachable)

Queries live production metrics over a configurable window (default: last 30 minutes).

| Signal | Threshold | What it catches |
|---|---|---|
| Block rate deviation | > 15 percentage points from training `fraud_rate` | Miscalibrated model |
| SLA breach rate | > 5% of decisions > 200 ms | Latency regression |

### Rollback mechanism

Every time a model is promoted, the previous champion version is saved in `local_store/model_registry/PREVIOUS`. If a health check fails, `registry.rollback()` promotes the previous version and clears `PREVIOUS` to prevent double-rollback.

```bash
# Run a health check against a running Prometheus instance
python scripts/auto_rollback.py \
  --prometheus-url http://localhost:9090 \
  --window-minutes 30

# Dry-run: see what would happen without making changes
python scripts/auto_rollback.py --dry-run

# Custom result output
python scripts/auto_rollback.py --output health_check.json
```

`rollback_result.json` written after every run:

```json
{
  "run_at": "2026-05-06T10:05:00",
  "current_version": "v1.1.0",
  "current_auc": 0.9344,
  "current_precision": 0.8712,
  "previous_version": "v1.0.0",
  "offline_failures": [],
  "online_failures": [],
  "rollback_triggered": false,
  "rolled_back_to": null,
  "dry_run": false
}
```

Exit codes: `0` = healthy, `1` = fatal error, `2` = rollback triggered.

---

## 8. Drift Detection

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

PSI thresholds:

| PSI | Meaning |
|---|---|
| < 0.10 | STABLE — no action needed |
| 0.10 – 0.20 | MONITOR — watch next window |
| > 0.20 | RETRAIN_REQUIRED — trigger challenger |

To simulate a concept drift before testing the retrain loop:

```bash
python scripts/inject_drift.py
```

This inserts a month of "Month 2 fraud" — known device + clean VPN IP + daytime + split amounts — causing PSI to spike on `device_trust_score`, `ip_fraud_history`, and `is_late_night`.

---

## 9. Grafana Dashboard

Open **http://localhost:3000** → login with `admin / fraudshield` → the **FraudShield — Model Observability** dashboard loads automatically.

Dashboard panels (12 total):

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
| Rollback Target | Previous champion (rollback destination if triggered) |
| Block Rate % — Drift from Training Baseline | Live block rate vs rollback threshold (+15 pp) |
| SLA Breach Rate | Breach rate vs rollback threshold (5%) |

The dashboard refreshes every **10 seconds** and defaults to the **last 1 hour** window.

---

## 10. Prometheus Metrics

The API exposes metrics at `/metrics` (standard Prometheus scrape format). Scraped every 15 seconds by the Prometheus container.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `fraudshield_decisions_total` | Counter | `decision`, `strategy` | Total scoring decisions |
| `fraudshield_score` | Histogram | — | Raw fraud score distribution (buckets 0–1) |
| `fraudshield_scoring_latency_seconds` | Histogram | — | End-to-end scoring time (buckets 5ms–1s) |
| `fraudshield_sla_breaches_total` | Counter | — | Decisions that took > 200 ms |
| `fraudshield_model_info` | Gauge | `version`, `strategy` | Currently loaded model (always 1) |

Useful PromQL queries:

```promql
# Live block rate %
100 * sum(rate(fraudshield_decisions_total{decision="block"}[5m]))
    / sum(rate(fraudshield_decisions_total[5m]))

# p95 latency
histogram_quantile(0.95,
  sum(rate(fraudshield_scoring_latency_seconds_bucket[5m])) by (le))

# SLA breach rate
sum(rate(fraudshield_sla_breaches_total[5m]))
  / sum(rate(fraudshield_decisions_total[5m]))

# Which model is live
fraudshield_model_info
```

---

## 11. Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_api.py -v
pytest tests/test_scoring.py -v
pytest tests/test_repository.py -v
```

Tests require the `local_store/` directory to exist. If running locally for the first time:

```bash
mkdir -p local_store/model_registry local_store/mlruns
pytest tests/ -v
```

---

## 12. API Reference

All endpoints (except `/health` and `/metrics`) require:

```
Authorization: Bearer dev_token_fraudshield_local_only
```

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — returns strategy + version |
| `GET` | `/metrics` | Prometheus scrape endpoint |
| `POST` | `/v1/transactions/score` | Score a transaction for CNP fraud |
| `GET` | `/v1/transactions` | List recent scored transactions |
| `GET` | `/v1/transactions/{id}` | Fetch a single transaction + score |
| `GET` | `/v1/users/{id}` | Fetch user profile |
| `PATCH` | `/v1/users/{id}/risk-tier` | Analyst overrides risk tier |
| `POST` | `/v1/labels` | Submit a fraud label (chargeback / review) |
| `GET` | `/v1/model/info` | Current champion model metadata |
| `GET` | `/v1/model/versions` | All versions in the registry |
| `GET` | `/v1/drift/report` | Run PSI drift report (live) |
| `GET` | `/v1/dashboard/stats` | 24-hour scoring summary |

Interactive docs: **http://localhost:8000/docs**

---

## 13. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                FraudShield — Full Architecture                 │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Payment Gateway                                               │
│       │ POST /v1/transactions/score                            │
│       ▼                                                        │
│  [fraud_api/main.py]   ←── Bearer auth + idempotency key       │
│       │ authenticate → validate → process → emit metrics       │
│       ▼                                                        │
│  [FraudService]                                                │
│       ├── UserRepository.get_by_id()     → SQLite / Postgres   │
│       ├── FeatureBuilder.build()         → Redis + DB          │
│       ├── FraudScorer.score()            → Strategy (below)    │
│       └── EventPublisher.publish()       → Observers           │
│                                                                │
│  [Scoring Strategy — swap one line in main.py]                 │
│       ├── RuleBasedStrategy   (fallback, no model needed)      │
│       └── XGBoostStrategy     (calibrated, SHAP reason codes)  │
│                                                                │
│  [Redis]   velocity counters (1h/6h/24h) + offline features   │
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

## 14. Project Structure

```
fraudshield_mvp/
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
│   │   ├── feature_builder.py  ← Builds FeatureVector from Transaction
│   │   └── strategies/
│   │       ├── rule_based.py   ← Deterministic rules + SHAP-style codes
│   │       └── xgboost_strategy.py ← Calibrated XGBoost + SHAP
│   ├── events/                 ← Observer pattern
│   │   ├── publisher.py        ← InMemoryPublisher (→ RedisStreams in prod)
│   │   └── observers.py        ← audit_log, alert, risk_tier_updater
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
│       └── drift.py            ← PSI drift detection (Evidently)
│
├── fraudshield_core/           ← Domain layer (no framework deps)
│   ├── models.py               ← All domain models (Transaction, FeatureVector, …)
│   ├── config.py               ← Env-var config (dotenv)
│   ├── db.py                   ← SQLAlchemy engine + table creation
│   └── redis_client.py         ← Redis connection
│
├── scripts/                    ← CLI tools
│   ├── seed_data.py            ← Populate DB with 90 days of synthetic data
│   ├── train_model.py          ← Train + register champion
│   ├── score_transaction.py    ← Interactive terminal scorer + labeler
│   ├── check_drift.py          ← PSI drift report
│   ├── inject_drift.py         ← Simulate concept drift (Month 2 fraud)
│   ├── retrain_loop.py         ← Drift → challenger → champion/challenger → promote
│   └── auto_rollback.py        ← Post-promotion health check + rollback
│
├── monitoring/                 ← Observability config
│   ├── prometheus.yml          ← Scrape config (fraud-api:8000/metrics, 15s)
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/    ← Auto-provisions Prometheus datasource
│       │   └── dashboards/     ← Auto-provisions dashboard location
│       └── dashboards/
│           └── fraudshield.json ← 12-panel Model Observability dashboard
│
├── .github/workflows/
│   ├── test.yml                ← pytest on every push/PR
│   ├── deploy.yml              ← Deploy gate (runs after tests)
│   ├── retrain_loop.yml        ← Daily cron drift → retrain → promote
│   └── auto_rollback.yml       ← Post-promotion health check → rollback
│
├── tests/
│   ├── test_api.py             ← FastAPI route tests (httpx)
│   ├── test_scoring.py         ← Scoring strategy + rule tests
│   └── test_repository.py      ← Repository layer tests
│
├── docker-compose.yml          ← Redis, Postgres, Prometheus, Grafana
├── requirements.txt
├── setup.py
└── local_store/                ← Runtime artifacts (gitignored)
    ├── fraud.db                ← SQLite database
    ├── model_registry/         ← Versioned model artifacts
    │   ├── v1.0.0/
    │   │   ├── model.pkl
    │   │   └── metadata.json
    │   ├── CURRENT             ← Points to champion version
    │   └── PREVIOUS            ← Points to pre-promotion champion (rollback target)
    └── mlruns/                 ← MLflow experiment tracking
```

---

## 15. Production Scaling

The architecture is designed so each component is a one-line swap in `fraud_api/main.py`. Business logic — `FraudService`, `FraudScorer`, all domain models — never changes.

| Component | MVP (now) | Production swap | Where to change |
|---|---|---|---|
| Database | SQLite | PostgreSQL | `main.py`: swap `SQLiteUserRepository` → `PostgreSQLUserRepository` |
| Event bus | In-memory dict | Kafka / Redis Streams | `main.py`: swap `InMemoryPublisher` → `RedisStreamsPublisher` |
| Scoring strategy | XGBoost local | Remote model server | `main.py`: swap `XGBoostStrategy` → `RemoteModelStrategy` |
| Model registry | Local files | MLflow server | `registry.py`: swap `LocalFileRegistry` → `MLflowRegistry` |
| Feature store | Redis dict | Feast / Tecton | `feature_builder.py`: swap Redis lookups → Feast SDK calls |
| Scheduler | GitHub Actions cron | Apache Airflow | Add `dags/retrain_dag.py` |
| Observability | Prometheus + Grafana | Same — already production-grade | No change needed |
