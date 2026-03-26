# FraudShield MVP
### Real-Time CNP Fraud Detection System

A production-ready MVP for Card-Not-Present fraud detection.
Built with clean architecture: Repository + Strategy + Observer patterns.
Designed to scale from SQLite on your laptop to PostgreSQL + Kafka in production — **zero rewrites**.

---

## Quick Start (5 commands)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start Redis (only external dependency)
docker-compose up -d redis

# 3. Seed the database with 90 days of synthetic data
python scripts/seed_data.py

# 4. Train the XGBoost model
python scripts/train_model.py

# 5. Start the API
python fraud_api/main.py
```

API runs at: `http://localhost:8000`
Docs at:     `http://localhost:8000/docs`

---

## The MLOps Loop (repeat this)

```
1. Score transactions  →  python scripts/score_transaction.py
2. Label decisions     →  press L (legit) or F (fraud) in terminal
3. Check drift         →  python scripts/check_drift.py
4. Retrain if needed   →  python scripts/train_model.py
5. Repeat
```

---

## Score a Transaction (curl)

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
    {"code": "high_risk_ip",      "contribution": 0.40},
    {"code": "amount_spike",      "contribution": 0.25},
    {"code": "high_risk_merchant","contribution": 0.20},
    {"code": "new_device",        "contribution": 0.30}
  ],
  "model_version": "v1.0.0",
  "latency_ms": 47,
  "request_id": "req_abc123"
}
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│              FraudShield MVP Architecture                │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Payment Gateway                                         │
│       │ POST /v1/transactions/score                      │
│       ▼                                                  │
│  [fraud_api/main.py]                                     │
│       │ authenticate → validate → process                │
│       ▼                                                  │
│  [FraudService]                                          │
│       ├── UserRepository.get_by_id()  → SQLite           │
│       ├── FeatureBuilder.build()      → Redis + DB       │
│       ├── FraudScorer.score()         → XGBoost/Rules    │
│       └── EventPublisher.publish()   → Observers         │
│                                                          │
│  [Redis]  velocity counters + offline features           │
│  [SQLite] users, transactions, scores, labels            │
│                                                          │
├──────────────────────────────────────────────────────────┤
│                  MLOps Loop (manual MVP)                 │
│                                                          │
│  scripts/seed_data.py            → populate DB               │
│  scripts/train_model.py          → train XGBoost + register  │
│  scripts/score_transaction.py    → review + label decisions  │
│  scripts/check_drift.py          → PSI drift detection       │
└──────────────────────────────────────────────────────────┘
```

---

## Production Scaling (one line per swap)

| Component | MVP | Production | Where to change |
|-----------|-----|-----------|-----------------|
| Database | SQLite | PostgreSQL | `fraud_api/main.py` line 1 |
| Feature Store | Redis dict | Feast | `fraud_api/main.py` line 2 |
| Event Bus | In-memory | Kafka/Redis Streams | `fraud_api/main.py` line 3 |
| Scheduler | Manual scripts | Airflow DAGs | Add `dags/` folder |
| Model Registry | Local files | MLflow server | `ml_pipeline/training/registry.py` |

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
fraudshield/
├── fraud_api/          ← Core scoring API (FastAPI)
│   ├── repository/     ← Storage abstraction (Repository pattern)
│   ├── scoring/        ← ML scoring (Strategy pattern)
│   └── events/         ← Downstream notifications (Observer pattern)
├── ml_pipeline/        ← MLOps components
│   ├── data/           ← Synthetic data generator
│   ├── features/       ← Feature store + precomputation
│   ├── training/       ← Train + registry
│   └── monitoring/     ← Drift detection (PSI)
├── fraudshield_core/   ← Domain models, DB, config
├── scripts/            ← CLI tools (seed, train, review, drift)
├── local_store/        ← Runtime artifacts (DB, model registry, MLflow)
├── tests/              ← Unit tests
└── notebooks/          ← EDA and experimentation
```
