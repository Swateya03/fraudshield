<div align="center">

# ⚡ FraudShield 

### Real-Time Card-Not-Present Fraud Detection

![XGBoost + PU Learning](https://img.shields.io/badge/Model-XGBoost%20%2B%20PU%20Learning-f85149?style=for-the-badge)
![AUC-ROC](https://img.shields.io/badge/AUC--ROC-0.94-3fb950?style=for-the-badge)
![PSI Drift Detection](https://img.shields.io/badge/PSI-Drift%20Detection-58a6ff?style=for-the-badge)

![Auto-Rollback](https://img.shields.io/badge/Auto--Rollback-Enabled-bc8cff?style=for-the-badge)
![SHAP](https://img.shields.io/badge/SHAP-Explanations-d29922?style=for-the-badge)
![A/B](https://img.shields.io/badge/A%2FB-Champion%2FChallenger-39d3f0?style=for-the-badge)

</div>

---

Most fraud ML systems are trained on **confirmed fraud labels** — but in production, most transactions are unlabeled: not confirmed fraud, but not confirmed legitimate either. FraudShield addresses this with **Elkan-Noto Positive-Unlabeled (PU) Learning**, treating unlabeled transactions as soft negatives weighted by confidence rather than hard negatives.

Full MLOps loop automated: **PSI drift detection → challenger training → AUC comparison → promotion → health check → auto-rollback**. Clean architecture: Repository + Strategy + Observer patterns. SQLite → PostgreSQL / Kafka in prod — no code changes.

---

## 📑 Table of Contents

| Overview | ML System | MLOps | Production |
|---|---|---|---|
| [Why It's Built This Way](#-01--why-its-built-this-way) | [PU Learning](#-04--elkan-noto-pu-learning) | [Drift Detection](#-10--psi-drift-detection) | [Production Readiness](#-13--production-readiness) |
| [Setup](#-02--setup) | [Results](#-05--results) | [Full MLOps Loop](#-11--full-mlops-loop) | [Path to Production](#-14--path-to-production) |
| [Quick Start](#-03--quick-start) | [Decision Thresholds](#-06--decision-thresholds) | [Auto-Rollback](#-12--auto-rollback) | [Known Limitations](#-15--known-limitations) |
| | [Feature Engineering](#-07--feature-engineering--21-features) | | |
| | [API Reference](#-08--api-reference) | | |
| | [Dashboard](#-09--dashboard-pages) | | |

---

## 🏗 01 — Why It's Built This Way

| | Decision | Rationale |
|---|---|---|
| 🧩 **Architecture** | Repository + Strategy Pattern | Every infrastructure dependency is behind an interface. The scoring strategy (`RuleBasedStrategy` / `XGBoostStrategy`) is swappable at startup. The API doesn't know which one it's using — tests run without a model file, and production hot-reloads a new champion without restarting via `POST /v1/model/reload`. |
| 🎓 **Training** | PU Learning over Supervised | Standard supervised training treats "not confirmed fraud" as legitimate. In practice ~40% of actual fraud is never labelled — chargebacks arrive late, small amounts go unreported. Elkan-Noto assigns confidence-weighted soft labels to unlabeled data, recovering accuracy lost to sparse-label bias. |
| 🎯 **Calibration** | Platt Scaling on XGBoost | XGBoost produces well-ranked outputs but poorly calibrated probabilities at extreme thresholds. The model is wrapped in `CalibratedClassifierCV` fitted on a held-out calibration set. The BLOCK threshold (0.85) must mean "85% probability of fraud" — uncalibrated raw scores cannot be interpreted this way. |
| 📊 **Evaluation** | GroupShuffleSplit by user_id | Train/test split is done by `user_id` group, not randomly. Random splitting leaks entity-level features (velocity history, user averages) from train to test, producing optimistic evaluation. GroupShuffleSplit ensures a user's transactions appear only in train or test, never both. |
| 🔁 **Idempotency** | Redis-Backed Deduplication | `Idempotency-Key` header deduplicates scoring requests in Redis with 24h TTL. Safe to retry on network failure — the same key always returns the same cached response. Prevents double-scoring on client retries, which would inflate velocity features. |
| 🧪 **Testing** | Hypothesis Property-Based Tests | Hypothesis generates 100 random `FeatureVector` instances and asserts scoring invariants: score always in [0,1], decision always consistent with thresholds, no NaN in output. Catches edge cases that hand-written unit tests miss — null velocities, extreme z-scores, boundary amounts. |

---

## ⚙️ 02 — Setup

```bash
cp .env.example .env          # set API_TOKEN before non-dev use

python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .              # required — makes fraud_api / fraudshield_core importable
```

---

## 🚀 03 — Quick Start

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

### 🔭 Full Stack (Prometheus + Grafana + Jaeger)

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

## 🧠 04 — Elkan-Noto PU Learning

Standard supervised learning for fraud requires clean binary labels. In production, most transactions are **unlabeled** — they haven't been confirmed as fraud, but they haven't been confirmed as legitimate either. Training on hard negatives introduces systematic false negative bias.

```python
# Step 1: Train initial model on all data (fraud=1, rest=0)
model_init.fit(X, y_partial)

# Step 2: Score unlabeled — assign soft negatives by confidence
probs_unlabeled = model_init.predict_proba(X_unlabeled)[:, 1]
confidence_weights = 1.0 - probs_unlabeled    # low prob = confident negative
soft_labels = (probs_unlabeled >= 0.5).astype(int)

# Step 3: Retrain on combined labeled + weighted unlabeled dataset
model_final.fit(X_combined, y_combined, sample_weight=weights)

# Step 4: Calibrate with Platt scaling on held-out set
calibrated = CalibratedClassifierCV(model_final, cv="prefit", method="sigmoid")
calibrated.fit(X_cal, y_cal)
```

> 💡 **The key insight:** an unlabeled transaction with a **low initial fraud score is confidently legitimate** — it gets high weight as a negative. One with a high score might actually be fraud — it gets low weight, reducing the noise it contributes to retraining.

---

## 📈 05 — Results

### PU Learning vs Standard Supervised

| Metric | Standard Supervised | PU Learning | Δ |
|---|---|---|---|
| AUC-ROC | 0.91 | **0.94** | 🟢 +0.03 |
| AUC-PR | 0.82 | **0.88** | 🟢 +0.06 |
| Precision @ BLOCK (0.85) | 0.87 | **0.91** | 🟢 +0.04 |
| Recall @ REVIEW (0.50) | 0.81 | **0.87** | 🟢 +0.06 |
| False Positive Rate | 13.2% | **9.1%** | 🟢 −4.1pp |

### Champion Model — Current Metrics

| Metric | Value | Threshold | Status |
|---|---|---|---|
| AUC-ROC | **0.94** | ≥ 0.70 (rollback floor) | 🟢 `HEALTHY` |
| AUC-PR | **0.88** | — | 🟢 `HEALTHY` |
| Precision @ BLOCK | **0.91** | ≥ 0.60 (rollback floor) | 🟢 `HEALTHY` |
| Recall @ BLOCK | **0.72** | — | 🟡 `NOMINAL` |
| Recall @ REVIEW | **0.87** | — | 🟢 `HEALTHY` |
| F1 @ BLOCK | **0.80** | — | 🟡 `NOMINAL` |
| Inference latency p99 | **< 50ms** | < 200ms SLA | 🟢 `HEALTHY` |

> \* Metrics from synthetic test set. Replace with real-world numbers once production labels accumulate via `/v1/labels`.

---

## 🎚 06 — Decision Thresholds

Thresholds were selected from the **precision-recall curve** on the held-out test set — not chosen arbitrarily. The BLOCK threshold targets precision ≥ 0.90 (customer experience) while REVIEW targets recall ≥ 0.85 (fraud capture).

**Fraud Probability → Decision Mapping**

```
 0.0 ──────────────── 0.50 ──────────────── 0.85 ──────────── 1.0
 │       ✅ ALLOW       │      🟡 REVIEW       │     🔴 BLOCK     │
                  Review floor           Block floor
```

Both thresholds are configurable via env vars (`FRAUD_THRESHOLD`, `REVIEW_THRESHOLD`) — adjustable without redeployment as the model is retrained on production labels. Configurable thresholds are a production requirement, not a nice-to-have.

---

## 🧬 07 — Feature Engineering — 21 Features

**Velocity features** are the highest-signal for Card-Not-Present fraud — account takeover typically generates a burst of transactions in a short window. These are computed from **Redis sorted sets at inference time** in <5ms per request. Amount features are normalised to INR and expressed as z-scores relative to the **user's own history**, not the population — a ₹1,00,000 transaction is normal for one user and suspicious for another.

| Group | Features |
|---|---|
| ⚡ **Velocity** (Redis-backed, real-time) — *highest signal* | `velocity_1h` · `velocity_24h` · `velocity_7d` · `user_avg_amount` · `user_std_amount` |
| 💰 **Amount** (normalised to INR) | `log_amount` · `amount_ratio` · `amount_zscore` · `is_round_amount` |
| 🚨 **Risk signals** | `merchant_risk` · `ip_risk` · `user_risk_tier` · `kyc_status` · `channel_risk` · `is_known_ip` |
| 📱 **Device / time** | `device_age_days` · `is_new_device` · `hour_of_day` · `day_of_week` · `is_weekend` · `is_night` |

---

## 🔌 08 — API Reference

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

### 💳 Score a Transaction

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

## 🖥 09 — Dashboard Pages

| Page | Route | What it shows |
|---|---|---|
| Live Feed | `/` | SSE push stream — every scored transaction in real time, pause/resume |
| Investigate | `/investigate` | Unified analyst workbench: user search, transaction table, SHAP codes, tier controls, audit trail |
| Score Explainer | `/explainer` | Submit any transaction, see probability + SHAP feature contributions (always dry-run) |
| Model Performance | `/model` | Champion metrics, version list, promote button, retrain trigger |
| Drift Monitor | `/drift` | PSI bar chart per feature, retrain recommendation, last-run timestamp |

---

## 📡 10 — PSI Drift Detection

Population Stability Index (PSI) compares the **last 7 days of transactions** against a 60-day baseline across 11 features. When max PSI exceeds 0.20, a retrain is automatically queued. PSI values are pushed to Prometheus so Grafana alert rules fire precisely.

```console
# Inject Month-2 concept drift (shifts amount + velocity distributions)
$ python scripts/inject_drift.py

# PSI report after drift injection
$ python scripts/check_drift.py

  amount_log      PSI: 0.34   RETRAIN_REQUIRED
  velocity_1h     PSI: 0.28   RETRAIN_REQUIRED
  hour_of_day     PSI: 0.07   STABLE
  is_round_amount PSI: 0.04   STABLE

  max_psi:        0.34  (threshold: 0.20)
  recommendation: RETRAIN_REQUIRED
```

| PSI Range | Status | Action |
|---|---|---|
| < 0.10 | 🟢 `STABLE` | No action |
| 0.10 – 0.20 | 🟡 `MONITOR` | Increase monitoring frequency |
| > 0.20 | 🔴 `RETRAIN_REQUIRED` | Auto-queue challenger training |

---

## 🔄 11 — Full MLOps Loop

The entire loop runs automated via GitHub Actions on a daily cron or triggers on drift detection. Every step produces a `retrain_result.json` artifact with PSI scores, AUC comparison, and promotion result.

```
┌───────────┐   ┌────────────┐   ┌───────────┐   ┌────────────┐   ┌─────────────┐   ┌────────────┐
│ PSI Drift │ → │   Train    │ → │  Compare  │ → │  Promote   │ → │   Health    │ → │  Rollback  │
│  detect   │   │ Challenger │   │    AUC    │   │  Champion  │   │    Check    │   │ if failed  │
│           │   │ background │   │   gated   │   │ if Δ≥0.01  │   │ offline+on  │   │    auto    │
└───────────┘   └────────────┘   └───────────┘   └────────────┘   └─────────────┘   └────────────┘
```

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

## 🛡 12 — Auto-Rollback

Every promotion saves the previous champion as `PREVIOUS`. The rollback script runs two check modes in sequence — if either fails, the previous version is automatically restored.

| Check Mode | What it validates | Thresholds |
|---|---|---|
| 🗄 **Offline Check** — Training Metric Thresholds | Validates champion's training metrics against minimum floors. Catches dangerously weak models promoted by accident. | • AUC-ROC ≥ 0.70<br>• Precision ≥ 0.60 |
| 📊 **Online Check (Prometheus)** — Live Production Metrics | Queries live metrics over a 30-minute window. Catches miscalibrated or slow models that passed offline checks. | • Block-rate deviation ≤ 15pp from training fraud_rate<br>• SLA breach rate ≤ 5% |

| Check | Threshold | Notes |
|---|---|---|
| AUC-ROC (offline) | < 0.70 | Catastrophically weak model |
| Precision (offline) | < 0.60 | Too many false positives |
| Block rate deviation (online) | > 15 pp from training fraud_rate | Miscalibrated |
| SLA breach rate (online) | > 5% requests > 200 ms | Latency regression |

```console
# Validated against a deliberately degraded model (AUC 0.68)
$ python scripts/auto_rollback.py --dry-run

  [OFFLINE] AUC-ROC:   0.68  <  0.70   FAIL
  [OFFLINE] Precision: 0.71  ≥  0.60   PASS

  Offline check FAILED. Previous champion available.
  Rolling back to v1.0.0...

Exit code: 2  (rollback triggered)
Rollback completed in 1.8s
```

Exit codes: `0` = healthy, `2` = rollback triggered.

---

## 📊 Prometheus Metrics

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

## 🧪 Running Tests

```bash
pytest tests/ -v --tb=short                   # unit + integration (no Redis needed)
docker-compose up -d redis
pytest tests/test_redis_features.py -v        # Redis velocity + idempotency tests
cd dashboard && npm test                      # frontend component tests (jsdom)
locust -f tests/locustfile.py --host http://localhost:8000   # load test
```

Test files: `test_api.py`, `test_scoring.py`, `test_repository.py`, `test_redis_features.py`, `test_scorer_properties.py`, `test_contract.py`.

---

## 🔧 Key Config (`.env`)

<details>
<summary><b>Click to expand full configuration table</b></summary>

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

</details>

---

## ✅ 13 — Production Readiness

Things most portfolio projects skip that are already built:

- ✅ **Idempotency** — `Idempotency-Key` header deduplicates scoring requests in Redis (24h TTL). Safe to retry on network failure.
- ✅ **SCD Type-2 Audit Trail** — Every analyst override to `user_risk_tier` is append-only — full history queryable via `/v1/users/{id}/history`.
- ✅ **Hot Model Reload** — `POST /v1/model/reload` promotes a new champion without restarting. Zero-downtime model updates.
- ✅ **K8s Health Probes** — `/health/live` (liveness) and `/health/ready` (readiness) return correct HTTP codes — drop-in for Kubernetes.
- ✅ **Rate Limiting** — 300 req/min per token with `X-RateLimit-*` response headers. `429` with Retry-After on breach.
- ✅ **Property-Based Tests** — Hypothesis generates 100 random feature vectors and asserts scoring invariants — score always in [0,1], decision always consistent.
- ✅ **OpenTelemetry** — Distributed tracing to Jaeger when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Zero-overhead when unset.
- ✅ **A/B Champion/Challenger** — Set `CHALLENGER_TRAFFIC_PCT` to route a fraction of traffic to a challenger. Auto-promotes when AUC delta ≥ threshold.

---

## 🛤 14 — Path to Production

One-line swaps in `main.py` — business logic never changes, only infrastructure bindings:

| Component | MVP | | Production |
|---|---|---|---|
| Database | `SQLiteUserRepository` | → | `PostgreSQLUserRepository` |
| Event bus | `InMemoryPublisher` | → | `RedisStreamsPublisher` / Kafka |
| Model registry | Local files + MLflow | → | SageMaker Model Registry |
| Feature store | Redis dict | → | Feast / Tecton |
| Scheduler | GitHub Actions cron | → | Airflow DAG |
| Secrets | `.env` file | → | Vault / AWS Secrets Manager |
| Observability | Prometheus + Grafana | → | Same — no change needed |

---

## ⚠️ 15 — Known Limitations

- ⚠️ **Synthetic training data** — the model has not been validated on real-world production transactions. AUC-ROC 0.94 and precision 0.91 are optimistic estimates on synthetic data. Real-world performance will differ until enough labelled chargebacks accumulate via `/v1/labels`.
- ⚠️ **Static FX rates** — currency normalisation uses fixed exchange rates (`FX_USD`, `FX_EUR` env vars). Stale rates introduce a systematic amount bias in INR-normalised features, particularly `amount_zscore`.
- ⚠️ **Cold-start users** — new users have no velocity history (`velocity_*` = 0, `user_avg_amount` = 0). The model falls back to merchant/channel/IP signals. Scores for first-time users are less reliable and tend toward the prior.
- ⚠️ **PSI detects feature drift, not label drift** — recall and precision degradation require human monitoring of analyst-labelled transactions. PSI can be stable while the model's discrimination deteriorates if fraud patterns change without distribution shift.
