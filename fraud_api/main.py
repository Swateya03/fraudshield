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

import asyncio
import uuid
import time
import json
import threading
from datetime import datetime
from typing import Optional
from dataclasses import asdict

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.wsgi import WSGIMiddleware
from pydantic import BaseModel, Field, field_validator
import uvicorn
from prometheus_client import make_wsgi_app

from fraudshield_core.models import Transaction, Channel
from fraudshield_core.config import config
from fraudshield_core.db import create_all_tables, get_engine
from fraudshield_core.redis_client import get_redis

# ── Repository implementations ──────────────
from fraud_api.repository.sqlite_repo import (
    SQLiteUserRepository,
    SQLiteTransactionRepository,
    SQLiteFraudScoreRepository,
    SQLiteFraudLabelRepository,
    SQLiteMerchantRepository,
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
from fraud_api.events.sse_broadcaster import broadcaster
from fraud_api.events.observers import (
    audit_log_observer, risk_tier_updater_observer, alert_observer
)

# ── Service ──────────────────────────────────
from fraud_api.services.fraud_service import FraudService
from ml_pipeline.training.registry import LocalFileRegistry
from fraud_api.metrics import record_score, set_model_version


# ─────────────────────────────────────────────
# Wire everything together
# To scale: swap any line below. Nothing else changes.
# ─────────────────────────────────────────────

engine        = get_engine()
user_repo     = SQLiteUserRepository(engine)
txn_repo      = SQLiteTransactionRepository(engine)
score_repo    = SQLiteFraudScoreRepository(engine)
label_repo    = SQLiteFraudLabelRepository(engine)
merchant_repo = SQLiteMerchantRepository(engine)
publisher     = InMemoryPublisher()
scorer        = FraudScorer(_strategy)
feat_builder  = FeatureBuilder(txn_repo)

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
    merchant_repo   = merchant_repo,
)


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title       = "FraudShield API",
    description = "Real-time CNP fraud detection",
    version     = "1.0.0",
)

# Expose Prometheus metrics at /metrics (standard scrape endpoint)
app.mount("/metrics", WSGIMiddleware(make_wsgi_app()))

_model_registry = LocalFileRegistry()
_serving_version: str = "none"  # version currently hot-loaded in scorer


def _serialize_model_metadata(m):
    d = asdict(m)
    d["trained_at"] = m.trained_at.isoformat()
    return d


def _load_current_champion() -> tuple:
    """Load the current champion from registry and hot-swap the scorer. Returns (success, version_or_error)."""
    global _serving_version
    try:
        new_strategy = XGBoostStrategy()
        if not new_strategy.is_ready():
            return False, "model file not found or failed to load"
        scorer.set_strategy(new_strategy)
        _, meta = _model_registry.load("current")
        _serving_version = meta.version
        set_model_version(meta.version, scorer.strategy_name)
        return True, meta.version
    except FileNotFoundError:
        return False, "no champion in registry"
    except Exception as e:
        return False, str(e)


def _poll_for_model_update() -> None:
    """Daemon thread: every 30 s check if a new champion has been promoted."""
    while True:
        time.sleep(30)
        try:
            candidate = _model_registry._get_current_version()
            if candidate and candidate != _serving_version:
                print(f"  [poll] New champion detected: {candidate} (was {_serving_version})")
                ok, msg = _load_current_champion()
                if ok:
                    print(f"  [poll] Hot-swapped to {msg}")
                else:
                    print(f"  [poll] Reload failed: {msg}")
        except Exception as e:
            print(f"  [poll] Check failed: {e}")


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
    dry_run:          bool          = False

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
    global _serving_version
    create_all_tables()
    try:
        _, meta = _model_registry.load("current")
        set_model_version(meta.version, scorer.strategy_name)
        _serving_version = meta.version
    except FileNotFoundError:
        set_model_version("none", scorer.strategy_name)
    threading.Thread(target=_poll_for_model_update, daemon=True, name="model-poller").start()
    print("[OK] FraudShield API started")
    print(f"  Strategy: {scorer.strategy_name}")
    print(f"  Metrics:  http://localhost:{config.API_PORT}/metrics")
    print(f"  Docs:     http://localhost:{config.API_PORT}/docs")


@app.get("/health")
async def health():
    """Circuit breaker health check endpoint."""
    return {
        "status":   "ok",
        "strategy": scorer.strategy_name,
        "version":  "1.0.0",
    }


@app.get("/v1/model/versions")
async def model_versions(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List all model versions in local registry (from local_store/model_registry/)."""
    _authenticate(authorization)
    versions = _model_registry.list_versions()
    return {"versions": [_serialize_model_metadata(v) for v in versions]}


@app.get("/v1/model/info")
async def model_info(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Champion model metadata (CURRENT file, else latest)."""
    _authenticate(authorization)
    versions = _model_registry.list_versions()
    if not versions:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NO_MODEL", "message": "No trained models in registry"}},
        )
    champion = next((v for v in versions if v.is_champion), None) or versions[-1]
    return _serialize_model_metadata(champion)


@app.post("/v1/model/reload")
async def reload_model(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Force-reload the current champion from the registry without restarting the process."""
    _authenticate(authorization)
    ok, msg = _load_current_champion()
    if not ok:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "RELOAD_FAILED", "message": msg}},
        )
    return {"reloaded": True, "version": msg, "strategy": scorer.strategy_name}


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
    if idempotency_key:
        try:
            cached = get_redis().get(f"idempotency:{idempotency_key}")
            if cached:
                return JSONResponse(
                    content=json.loads(cached),
                    headers={"X-Idempotent-Replay": "true"},
                )
        except Exception:
            pass  # Redis unavailable — proceed without idempotency check

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
    fraud_score = service.process(txn, dry_run=body.dry_run)

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

    # Cache for idempotency (skip for dry-run — nothing was persisted)
    if idempotency_key and not body.dry_run:
        try:
            get_redis().setex(
                f"idempotency:{idempotency_key}",
                config.REDIS_TTL_IDEMPOTENCY,
                json.dumps(response),
            )
        except Exception:
            pass  # Redis unavailable — score already returned, continue

    # Push to SSE stream (skip dry-run — nothing was persisted)
    if not body.dry_run:
        broadcaster.broadcast({
            "transaction_id":    txn.id,
            "user_id":           txn.user_id,
            "merchant_id":       txn.merchant_id,
            "amount":            txn.amount,
            "currency":          txn.currency,
            "channel":           txn.channel.value,
            "ip_address":        txn.ip_address,
            "fraud_probability": fraud_score.score,
            "decision":          fraud_score.decision.value,
            "reason_codes":      fraud_score.reason_codes,
            "model_version":     fraud_score.model_version,
            "latency_ms":        fraud_score.latency_ms,
            "scored_at":         fraud_score.scored_at.isoformat(),
        })

    # Emit Prometheus metrics
    record_score(
        decision   = fraud_score.decision.value,
        strategy   = scorer.strategy_name,
        score      = fraud_score.score,
        latency_ms = fraud_score.latency_ms,
    )

    # SLA warning
    if fraud_score.latency_ms > 200:
        print(f"  ⚠️  SLA BREACH: {fraud_score.latency_ms}ms > 200ms")

    return response


@app.get("/v1/transactions")
async def list_transactions(
    limit:         int = 20,
    order:         str = "desc",
    user_id:       Optional[str] = None,
    decision:      Optional[str] = None,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List recently scored transactions (for Live Feed & Explorer)."""
    _authenticate(authorization)
    from sqlalchemy import select, asc, desc
    from fraudshield_core.db import transactions_table as t, fraud_scores_table as fs

    stmt = (
        select(
            t.c.id.label("transaction_id"),
            t.c.user_id,
            t.c.merchant_id,
            t.c.amount,
            t.c.currency,
            t.c.channel,
            t.c.ip_address,
            fs.c.score.label("fraud_probability"),
            fs.c.decision,
            fs.c.reason_codes,
            fs.c.strategy_used,
            fs.c.model_version,
            fs.c.latency_ms,
            fs.c.scored_at,
        )
        .select_from(fs.join(t, t.c.id == fs.c.transaction_id))
    )

    if user_id:
        stmt = stmt.where(t.c.user_id == user_id)
    if decision:
        stmt = stmt.where(fs.c.decision == decision)

    stmt = stmt.order_by(desc(fs.c.scored_at) if order == "desc" else asc(fs.c.scored_at))
    stmt = stmt.limit(min(limit, 500))

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()

    import json as _json
    items = []
    for r in rows:
        d = dict(r._mapping)
        if isinstance(d.get("reason_codes"), str):
            try:
                d["reason_codes"] = _json.loads(d["reason_codes"])
            except Exception:
                d["reason_codes"] = []
        items.append(d)
    return {"transactions": items}


@app.get("/v1/dashboard/stats")
async def dashboard_stats(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """24-hour scoring summary for Live Feed stat cards."""
    _authenticate(authorization)
    from sqlalchemy import text as sa_text
    eng = get_engine()
    sql = sa_text("""
        SELECT
            COUNT(*)                                              AS total_24h,
            SUM(CASE WHEN decision = 'block'  THEN 1 ELSE 0 END) AS blocked_24h,
            SUM(CASE WHEN decision = 'review' THEN 1 ELSE 0 END) AS review_24h,
            SUM(CASE WHEN decision = 'allow'  THEN 1 ELSE 0 END) AS allowed_24h
        FROM fraud_scores
        WHERE scored_at >= datetime('now', '-1 day')
    """)
    with eng.connect() as conn:
        row = conn.execute(sql).fetchone()
    if not row:
        return {"total_24h": 0, "blocked_24h": 0, "review_24h": 0, "allowed_24h": 0}
    return dict(row._mapping)


@app.get("/v1/stream")
async def stream_transactions(token: Optional[str] = None):
    """
    Server-Sent Events stream — one event per scored transaction.
    Auth via ?token= query param (EventSource cannot set headers).
    """
    if not token or token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def generator():
        q = broadcaster.subscribe()
        try:
            yield ": connected\n\n"          # immediate ack so browser knows it's live
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy / browser 30s timeout
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",      # disable nginx buffering if behind a proxy
        },
    )


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


@app.get("/v1/users/{user_id}")
async def get_user(
    user_id:       str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Fetch user profile for Risk Manager page."""
    _authenticate(authorization)
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "RESOURCE_NOT_FOUND",
                               "message": f"User {user_id} not found"}}
        )
    return {
        "id":         user.id,
        "email":      user.email,
        "phone":      user.phone,
        "risk_tier":  user.risk_tier.value,
        "kyc_status": user.kyc_status.value,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@app.post("/v1/labels")
async def submit_label(
    body:          dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Submit a fraud label (chargeback or analyst review)."""
    _authenticate(authorization)
    from fraudshield_core.models import FraudLabel, LabelSource
    txn_id   = body.get("transaction_id")
    is_fraud = body.get("is_fraud")
    if txn_id is None or is_fraud is None:
        raise HTTPException(status_code=400,
            detail={"error": {"code": "MISSING_REQUIRED_FIELD",
                               "message": "transaction_id and is_fraud are required"}})
    label = FraudLabel(
        id             = str(uuid.uuid4()),
        transaction_id = txn_id,
        is_fraud       = bool(is_fraud),
        label_source   = LabelSource.MANUAL_REVIEW,
        labeled_at     = datetime.utcnow(),
        labeled_by     = "analyst",
        notes          = body.get("notes"),
    )
    label_repo.save(label)
    return {"transaction_id": txn_id, "is_fraud": is_fraud, "saved": True}


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


@app.get("/v1/drift/report")
async def drift_report(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Run PSI drift detection and return report."""
    _authenticate(authorization)
    from ml_pipeline.monitoring.drift import run_drift_report
    report = run_drift_report()
    if "error" in report:
        raise HTTPException(status_code=422, detail={"error": {"code": "DRIFT_ERROR", "message": report["error"]}})
    return report


if __name__ == "__main__":
    uvicorn.run("fraud_api.main:app",
                host=config.API_HOST,
                port=config.API_PORT,
                reload=True)
