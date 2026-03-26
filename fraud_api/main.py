"""
fraud_api/main.py
──────────────────
FastAPI application — wire-up and routes.

THIS is where you swap implementations (slide 76 dependency injection).
Business logic never changes. Only this file changes when scaling.

MVP wiring:
  SQLiteUserRepository    → PostgreSQLUserRepository
  InMemoryPublisher       → RedisStreamsPublisher
  RuleBasedStrategy       → XGBoostStrategy
  (one line change each)
"""

import uuid
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn

from fraudshield_core.models import Transaction, Channel
from fraudshield_core.config import config
from fraudshield_core.db import create_all_tables, get_engine

# ── Repository implementations ──────────────
from fraud_api.repository.sqlite_repo import (
    SQLiteUserRepository,
    SQLiteTransactionRepository,
    SQLiteFraudScoreRepository,
    SQLiteFraudLabelRepository,
)

# ── Scoring ──────────────────────────────────
from fraud_api.scoring.scorer import FraudScorer
from fraud_api.scoring.feature_builder import FeatureBuilder
from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy

# Try to load XGBoost strategy, fall back to rule-based if model not ready
try:
    from fraud_api.scoring.strategies.xgboost_strategy import XGBoostStrategy
    _strategy = XGBoostStrategy()
    if not _strategy.is_ready():
        print("  [main] XGBoost model not ready — using rule-based fallback")
        _strategy = RuleBasedStrategy()
except Exception as e:
    print(f"  [main] XGBoost load failed ({e}) — using rule-based fallback")
    _strategy = RuleBasedStrategy()

# ── Events ───────────────────────────────────
from fraud_api.events.publisher import InMemoryPublisher
from fraud_api.events.observers import (
    audit_log_observer, risk_tier_updater_observer, alert_observer
)

# ── Service ──────────────────────────────────
from fraud_api.services.fraud_service import FraudService


# ─────────────────────────────────────────────
# Wire everything together
# To scale: swap any line below. Nothing else changes.
# ─────────────────────────────────────────────

engine      = get_engine()
user_repo   = SQLiteUserRepository(engine)
txn_repo    = SQLiteTransactionRepository(engine)
score_repo  = SQLiteFraudScoreRepository(engine)
label_repo  = SQLiteFraudLabelRepository(engine)
publisher   = InMemoryPublisher()
scorer      = FraudScorer(_strategy)
feat_builder= FeatureBuilder(txn_repo)

# Subscribe observers
publisher.subscribe(audit_log_observer)
publisher.subscribe(alert_observer)

service = FraudService(
    user_repo       = user_repo,
    txn_repo        = txn_repo,
    score_repo      = score_repo,
    scorer          = scorer,
    feature_builder = feat_builder,
    publisher       = publisher,
)


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title       = "FraudShield API",
    description = "Real-time CNP fraud detection",
    version     = "1.0.0",
)

# Idempotency store (Redis in prod, dict in MVP)
_idempotency_store: dict = {}


# ─────────────────────────────────────────────
# Request / Response Schemas (Pydantic)
# ─────────────────────────────────────────────

class ScoreRequest(BaseModel):
    transaction_id:   str   = Field(..., min_length=1, max_length=128)
    user_id:          str   = Field(..., min_length=1)
    merchant_id:      str   = Field(..., min_length=1)
    amount:           float = Field(..., gt=0, le=10_000_000)
    currency:         str   = Field(default="INR")
    channel:          str   = Field(...)
    device_id:        Optional[str] = None
    ip_address:       Optional[str] = None

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v):
        if v not in {"INR", "USD", "EUR"}:
            raise ValueError(f"currency must be INR/USD/EUR, got {v}")
        return v

    @field_validator("channel")
    @classmethod
    def valid_channel(cls, v):
        valid = {c.value for c in Channel}
        if v not in valid:
            raise ValueError(f"channel must be one of {valid}, got {v}")
        return v


class ScoreResponse(BaseModel):
    transaction_id:    str
    fraud_probability: float
    decision:          str
    reason_codes:      list
    model_version:     str
    latency_ms:        int
    request_id:        str


# ─────────────────────────────────────────────
# Middleware: Auth
# ─────────────────────────────────────────────

def _authenticate(authorization: Optional[str]) -> None:
    """Validates Bearer token. Raises 401 on failure."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error": {
                "code":    "MISSING_AUTH_TOKEN",
                "message": "Authorization header is required"
            }}
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {
                "code":    "INVALID_AUTH_TOKEN",
                "message": "Authorization must be 'Bearer <token>'"
            }}
        )
    token = authorization[7:]
    if token != config.API_TOKEN:
        raise HTTPException(
            status_code=401,
            detail={"error": {
                "code":    "INVALID_AUTH_TOKEN",
                "message": "Invalid or expired token"
            }}
        )


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    create_all_tables()
    print("✓ FraudShield API started")
    print(f"  Strategy: {scorer.strategy_name}")
    print(f"  Docs:     http://localhost:{config.API_PORT}/docs")


@app.get("/health")
async def health():
    """Circuit breaker health check endpoint."""
    return {
        "status":   "ok",
        "strategy": scorer.strategy_name,
        "version":  "1.0.0",
    }


@app.post("/v1/transactions/score", response_model=ScoreResponse)
async def score_transaction(
    body:             ScoreRequest,
    authorization:    Optional[str] = Header(None, alias="Authorization"),
    idempotency_key:  Optional[str] = Header(None, alias="Idempotency-Key"),
    x_request_id:     Optional[str] = Header(None, alias="X-Request-ID"),
):
    """
    Score a transaction for CNP fraud.

    - Auth: Bearer token required
    - Idempotency: include Idempotency-Key header for safe retries
    - SLA: responds in under 200ms
    """
    request_id = x_request_id or str(uuid.uuid4())[:8]

    # Auth
    _authenticate(authorization)

    # Idempotency — return cached result if key seen before
    if idempotency_key and idempotency_key in _idempotency_store:
        cached = _idempotency_store[idempotency_key]
        return JSONResponse(
            content=cached,
            headers={"X-Idempotent-Replay": "true"}
        )

    # Build Transaction domain object
    txn = Transaction(
        id          = body.transaction_id,
        user_id     = body.user_id,
        merchant_id = body.merchant_id,
        device_id   = body.device_id,
        amount      = body.amount,
        currency    = body.currency,
        channel     = Channel(body.channel),
        ip_address  = body.ip_address,
        created_at  = datetime.utcnow(),
    )

    # Score
    fraud_score = service.process(txn)

    # Build response
    response = {
        "transaction_id":    txn.id,
        "fraud_probability": fraud_score.score,
        "decision":          fraud_score.decision.value,
        "reason_codes":      fraud_score.reason_codes,
        "model_version":     fraud_score.model_version,
        "latency_ms":        fraud_score.latency_ms,
        "request_id":        request_id,
    }

    # Cache for idempotency
    if idempotency_key:
        _idempotency_store[idempotency_key] = response

    # SLA warning
    if fraud_score.latency_ms > 200:
        print(f"  ⚠️  SLA BREACH: {fraud_score.latency_ms}ms > 200ms")

    return response


@app.get("/v1/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: str,
    authorization:  Optional[str] = Header(None, alias="Authorization"),
):
    """Fetch a transaction with its fraud score."""
    _authenticate(authorization)
    txn   = txn_repo.get_by_id(transaction_id)
    score = score_repo.get_by_transaction_id(transaction_id)
    if not txn:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "RESOURCE_NOT_FOUND",
                               "message": f"Transaction {transaction_id} not found"}}
        )
    return {"transaction": txn, "score": score}


@app.patch("/v1/users/{user_id}/risk-tier")
async def update_risk_tier(
    user_id:       str,
    body:          dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Analyst updates a user's risk tier manually."""
    _authenticate(authorization)
    new_tier = body.get("risk_tier")
    if not new_tier:
        raise HTTPException(status_code=400,
            detail={"error": {"code": "MISSING_REQUIRED_FIELD",
                               "field": "risk_tier"}})
    user_repo.update_risk_tier(user_id, new_tier,
                                changed_by="analyst",
                                reason=body.get("reason"))
    return {"user_id": user_id, "risk_tier": new_tier, "updated": True}


if __name__ == "__main__":
    uvicorn.run("fraud_api.main:app",
                host=config.API_HOST,
                port=config.API_PORT,
                reload=True)
