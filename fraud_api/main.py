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

import os

from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.wsgi import WSGIMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, field_validator
import uvicorn
from prometheus_client import make_wsgi_app

from fraudshield_core.models import Transaction, Channel

# ── OpenTelemetry — instrument before app is created ─────────────────────────
# Silently skipped when the SDK is not installed or OTEL_EXPORTER_OTLP_ENDPOINT
# is unset, so tests and bare local runs are unaffected.
try:
    _otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if _otel_endpoint:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        _resource = Resource.create({"service.name": "fraudshield-api",
                                     "service.version": "1.1.0"})
        _provider = TracerProvider(resource=_resource)
        _provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint, insecure=True))
        )
        trace.set_tracer_provider(_provider)
        # FastAPI routes and SQLAlchemy queries will be auto-instrumented
        # after the app/engine objects are created (see startup event).
        _otel_enabled = True
    else:
        _otel_enabled = False
except Exception:
    _otel_enabled = False
from fraudshield_core.config import config
from fraudshield_core.db import create_all_tables, get_engine
from fraudshield_core.redis_client import get_redis
from fraudshield_core.logging import get_logger, request_id_var

log = get_logger(__name__)

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

# ── Champion/Challenger A/B setup ────────────
_challenger_strategy = None
if config.CHALLENGER_MODEL_VERSION:
    try:
        _chal = XGBoostStrategy(version=config.CHALLENGER_MODEL_VERSION)
        if _chal.is_ready():
            _challenger_strategy = _chal
            print(f"  [main] Challenger loaded: {config.CHALLENGER_MODEL_VERSION} "
                  f"({config.CHALLENGER_TRAFFIC_PCT*100:.0f}% traffic)")
        else:
            print(f"  [main] Challenger {config.CHALLENGER_MODEL_VERSION} not ready — A/B disabled")
    except Exception as e:
        print(f"  [main] Challenger load failed ({e}) — A/B disabled")

# ── Events ───────────────────────────────────
from fraud_api.events.publisher import InMemoryPublisher
from fraud_api.events.sse_broadcaster import broadcaster, CHANNEL
from fraud_api.events.observers import (
    audit_log_observer, risk_tier_updater_observer, alert_observer
)

# ── Service ──────────────────────────────────
from fraud_api.services.fraud_service import FraudService
from ml_pipeline.training.registry import LocalFileRegistry
from fraud_api.metrics import record_score, set_model_version, set_rollback_target

import httpx as _httpx


async def _fire_webhook(payload: dict) -> None:
    """POST decision payload to WEBHOOK_URL with 3-attempt exponential backoff. Silently ignored if URL unset."""
    url = config.WEBHOOK_URL
    if not url:
        return
    decision = payload.get("decision", "")
    if decision not in config.WEBHOOK_EVENTS:
        return
    for attempt in range(3):
        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(url, json=payload)
                if r.status_code < 500:
                    return
        except Exception as exc:
            log.warning("webhook_attempt_failed", attempt=attempt + 1, error=str(exc))
        await asyncio.sleep(2 ** attempt)


# Guards concurrent retrains (drift-triggered or analyst-triggered).
_retrain_in_progress = threading.Event()


def _run_training_pipeline() -> None:
    """Execute training synchronously — called in a background thread."""
    try:
        from ml_pipeline.training.dataset import build_training_dataset
        from ml_pipeline.training.train import train
        from ml_pipeline.training.registry import LocalFileRegistry

        X, y, groups = build_training_dataset()
        model, metadata = train(X, y, groups=groups)
        registry = LocalFileRegistry()
        registry.save(model, metadata)
        registry.promote(metadata.version)
        log.info("retrain_complete", triggered_by="analyst", version=metadata.version)
    except Exception as exc:
        log.error("retrain_failed", error=str(exc))


async def _run_training_pipeline_guarded() -> None:
    """Async wrapper: clears the in-progress lock when training finishes."""
    try:
        await asyncio.to_thread(_run_training_pipeline)
    finally:
        _retrain_in_progress.clear()


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
if _challenger_strategy:
    scorer.set_challenger(_challenger_strategy, config.CHALLENGER_TRAFFIC_PCT)
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

def _token_rate_key(request: Request) -> str:
    """Rate-limit by bearer token; fall back to IP so unauthenticated calls are still throttled."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return f"token:{auth[7:]}"
    return get_remote_address(request)

limiter = Limiter(key_func=_token_rate_key, default_limits=["300/minute"])

app = FastAPI(
    title       = "FraudShield API",
    description = "Real-time CNP fraud detection",
    version     = "1.0.0",
)

_API_VERSION = "1.1.0"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Stamp every response with X-Request-ID and X-API-Version; set ContextVar for logging."""
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        token  = request_id_var.set(req_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-API-Version"] = _API_VERSION
        return response


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = config.CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["GET", "POST", "PATCH"],
    allow_headers     = [
        "Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID",
        # Expose slowapi rate-limit headers so the dashboard can display budget usage
        "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
        "Retry-After",
    ],
    expose_headers    = ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
                         "X-Request-ID", "X-API-Version", "X-Idempotent-Replay"],
)

# Expose Prometheus metrics at /metrics (standard scrape endpoint)
app.mount("/metrics", WSGIMiddleware(make_wsgi_app()))

_model_registry = LocalFileRegistry()
_serving_version:    str = "none"   # champion version currently hot-loaded
_challenger_version: str = ""       # challenger version when A/B is active


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
        prev_version = _model_registry.get_previous_version()
        if prev_version:
            try:
                _, prev_meta = _model_registry.load(prev_version)
                set_rollback_target(prev_meta.version, scorer.strategy_name)
            except Exception:
                pass
        return True, meta.version
    except FileNotFoundError:
        return False, "no champion in registry"
    except Exception as e:
        return False, str(e)


def _maybe_promote_challenger() -> None:
    """Auto-promote challenger when its AUC beats the champion by AB_PROMOTE_THRESHOLD."""
    champ_meta = next((v for v in _model_registry.list_versions() if v.is_champion), None)
    if not champ_meta:
        return
    try:
        _, chal_meta = _model_registry.load(config.CHALLENGER_MODEL_VERSION)
    except FileNotFoundError:
        return
    margin = chal_meta.auc_roc - champ_meta.auc_roc
    if margin >= config.AB_PROMOTE_THRESHOLD:
        log.info("ab_auto_promote",
                 challenger=config.CHALLENGER_MODEL_VERSION,
                 champion=champ_meta.version,
                 margin=round(margin, 4))
        _model_registry.promote(config.CHALLENGER_MODEL_VERSION)
        _load_current_champion()


def _poll_for_model_update() -> None:
    """Daemon thread: every 30 s check for a new champion; also runs A/B auto-promotion."""
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

        # A/B auto-promotion check
        if config.CHALLENGER_MODEL_VERSION and scorer._challenger is not None:
            try:
                _maybe_promote_challenger()
            except Exception as exc:
                print(f"  [poll] A/B check failed: {exc}")


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
    ab_variant:        str = "champion"


class TransactionItem(BaseModel):
    transaction_id:    str
    user_id:           str
    merchant_id:       str
    amount:            float
    currency:          str
    channel:           str
    ip_address:        Optional[str]
    fraud_probability: Optional[float]
    decision:          Optional[str]
    reason_codes:      list
    strategy_used:     Optional[str]
    model_version:     Optional[str]
    ab_variant:        Optional[str]
    latency_ms:        Optional[int]
    scored_at:         Optional[str]

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    items:       list[TransactionItem]
    next_cursor: Optional[str]
    count:       int


class UserResponse(BaseModel):
    id:         str
    email:      str
    phone:      Optional[str]
    risk_tier:  str
    kyc_status: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class StatsResponse(BaseModel):
    total_24h:   int
    blocked_24h: int
    review_24h:  int
    allowed_24h: int


class RetrainRequest(BaseModel):
    reason: str = Field(default="manual trigger", max_length=256)


class RetrainResponse(BaseModel):
    status:       str
    message:      str
    triggered_by: str


class BatchScoreRequest(BaseModel):
    transactions: list[ScoreRequest] = Field(..., min_length=1, max_length=100)


class BatchScoreItem(BaseModel):
    transaction_id:    str
    fraud_probability: Optional[float] = None
    decision:          Optional[str]   = None
    reason_codes:      list            = []
    model_version:     Optional[str]   = None
    latency_ms:        Optional[int]   = None
    ab_variant:        str             = "champion"
    error:             Optional[str]   = None


class BatchScoreResponse(BaseModel):
    results: list[BatchScoreItem]
    count:   int
    errors:  int


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
    # Read from env at request time so operators can rotate the token
    # by updating the env var without restarting the process.
    current_token = os.getenv("API_TOKEN", config.API_TOKEN)
    if token != current_token:
        raise HTTPException(
            status_code=401,
            detail={"error": {
                "code":    "INVALID_AUTH_TOKEN",
                "message": "Invalid or expired token"
            }}
        )


# ─────────────────────────────────────────────
# Sync DB helpers (called via asyncio.to_thread so the event loop is not blocked)
# ─────────────────────────────────────────────

def _query_transactions(limit: int, order: str,
                         user_id: Optional[str], decision: Optional[str],
                         cursor: Optional[str] = None) -> tuple:
    from datetime import datetime as _dt
    from sqlalchemy import select, asc, desc
    from fraudshield_core.db import transactions_table as t, fraud_scores_table as fs
    import json as _json

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
            fs.c.ab_variant,
        )
        .select_from(fs.join(t, t.c.id == fs.c.transaction_id))
    )
    if user_id:
        stmt = stmt.where(t.c.user_id == user_id)
    if decision:
        stmt = stmt.where(fs.c.decision == decision)
    if cursor:
        try:
            cursor_dt = _dt.fromisoformat(cursor)
            stmt = stmt.where(
                fs.c.scored_at < cursor_dt if order == "desc" else fs.c.scored_at > cursor_dt
            )
        except ValueError:
            pass  # malformed cursor — ignore and return from the start
    stmt = stmt.order_by(desc(fs.c.scored_at) if order == "desc" else asc(fs.c.scored_at))
    stmt = stmt.limit(min(limit, 500))

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()

    items = []
    for r in rows:
        d = dict(r._mapping)
        if isinstance(d.get("reason_codes"), str):
            try:
                d["reason_codes"] = _json.loads(d["reason_codes"])
            except Exception:
                d["reason_codes"] = []
        items.append(d)

    # next_cursor is the scored_at of the last row — client passes it back to page forward
    next_cursor = None
    if items:
        last_ts = items[-1].get("scored_at")
        if last_ts:
            next_cursor = last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts)

    return items, next_cursor


def _query_stats() -> dict:
    from sqlalchemy import text as sa_text
    sql = sa_text("""
        SELECT
            COUNT(*)                                              AS total_24h,
            SUM(CASE WHEN decision = 'block'  THEN 1 ELSE 0 END) AS blocked_24h,
            SUM(CASE WHEN decision = 'review' THEN 1 ELSE 0 END) AS review_24h,
            SUM(CASE WHEN decision = 'allow'  THEN 1 ELSE 0 END) AS allowed_24h
        FROM fraud_scores
        WHERE scored_at >= datetime('now', '-1 day')
    """)
    with get_engine().connect() as conn:
        row = conn.execute(sql).fetchone()
    if not row:
        return {"total_24h": 0, "blocked_24h": 0, "review_24h": 0, "allowed_24h": 0}
    d = dict(row._mapping)
    return {k: (v or 0) for k, v in d.items()}


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
    prev_version = _model_registry.get_previous_version()
    if prev_version:
        try:
            _, prev_meta = _model_registry.load(prev_version)
            set_rollback_target(prev_meta.version, scorer.strategy_name)
        except Exception:
            pass
    threading.Thread(target=_poll_for_model_update, daemon=True, name="model-poller").start()

    # Attach OTel instrumentors now that app + engine are live
    if _otel_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            FastAPIInstrumentor.instrument_app(app)
            SQLAlchemyInstrumentor().instrument(engine=engine)
            print(f"  Tracing:  {os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')}")
        except Exception as exc:
            print(f"  Tracing:  FAILED ({exc})")

    print("[OK] FraudShield API started")
    print(f"  Strategy: {scorer.strategy_name}")
    print(f"  Metrics:  http://localhost:{config.API_PORT}/metrics")
    print(f"  Docs:     http://localhost:{config.API_PORT}/docs")


@app.get("/health")
async def health():
    """
    Deep health check. Reports live status of DB, Redis, and the loaded model.
    Returns HTTP 200 with status=ok|degraded. Never raises 5xx so load balancers
    don't flap — callers should inspect the `status` field.
    """
    from sqlalchemy import text as _text

    checks: dict = {}

    # DB
    try:
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        checks["db"] = {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        checks["db"] = {"status": "error", "error": str(exc)}

    # Redis
    try:
        t0 = time.perf_counter()
        get_redis().ping()
        checks["redis"] = {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        checks["redis"] = {"status": "degraded", "error": str(exc)}

    # Model
    model_loaded = scorer._champion is not None
    checks["model"] = {
        "status":   "ok" if model_loaded else "degraded",
        "strategy": scorer.strategy_name,
        "version":  _serving_version,
    }

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "version": _API_VERSION, "checks": checks}


@app.get("/health/live")
async def health_live():
    """K8s liveness probe — returns 200 while the process is alive. Never checks dependencies."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """
    K8s readiness probe — returns 200 only when the API can serve traffic.
    Returns 503 if DB, Redis, or model are not operational, so K8s stops routing
    traffic to this replica without restarting it.
    """
    from sqlalchemy import text as _text

    failing: dict = {}

    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
    except Exception as exc:
        failing["db"] = str(exc)

    try:
        get_redis().ping()
    except Exception as exc:
        failing["redis"] = str(exc)

    if scorer._champion is None:
        failing["model"] = "no champion loaded"

    if failing:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "failing": failing})
    return {"status": "ready"}


@app.get("/v1/model/versions")
async def model_versions(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List all model versions in local registry (from local_store/model_registry/)."""
    _authenticate(authorization)
    versions = await asyncio.to_thread(_model_registry.list_versions)
    return {"versions": [_serialize_model_metadata(v) for v in versions]}


@app.get("/v1/model/info")
async def model_info(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Champion model metadata (CURRENT file, else latest)."""
    _authenticate(authorization)
    versions = await asyncio.to_thread(_model_registry.list_versions)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NO_MODEL", "message": "No trained models in registry"}},
        )
    champion = next((v for v in versions if v.is_champion), None) or versions[-1]
    return _serialize_model_metadata(champion)


@app.get("/v1/model/features")
async def model_feature_importances(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    XGBoost feature importances for the currently active champion model,
    sorted by gain (highest first). Includes the challenger if A/B is enabled.

    Useful for analyst transparency and detecting unexpected feature drift.
    Returns an empty list for the rule-based fallback strategy.
    """
    _authenticate(authorization)

    from fraud_api.scoring.strategies.xgboost_strategy import XGBoostStrategy as _XGB

    def _get_importances():
        champion_imp    = []
        challenger_imp  = []
        champion_ready  = False
        challenger_ready = False

        if isinstance(scorer._champion, _XGB):
            champion_imp   = scorer._champion.get_feature_importances()
            champion_ready = bool(champion_imp)

        if scorer._challenger and isinstance(scorer._challenger, _XGB):
            challenger_imp   = scorer._challenger.get_feature_importances()
            challenger_ready = bool(challenger_imp)

        return {
            "champion": {
                "strategy":    scorer._champion.name,
                "ready":       champion_ready,
                "importances": champion_imp,
            },
            "challenger": {
                "strategy":    scorer._challenger.name if scorer._challenger else None,
                "traffic_pct": scorer._challenger_pct,
                "ready":       challenger_ready,
                "importances": challenger_imp,
            } if scorer._challenger else None,
        }

    return await asyncio.to_thread(_get_importances)


@app.post("/v1/model/reload")
async def reload_model(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Force-reload the current champion from the registry without restarting the process."""
    _authenticate(authorization)
    ok, msg = await asyncio.to_thread(_load_current_champion)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "RELOAD_FAILED", "message": msg}},
        )
    return {"reloaded": True, "version": msg, "strategy": scorer.strategy_name}


@app.post("/v1/model/promote/{version}")
async def promote_model(
    version:       str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Promote an existing registry version to champion and hot-swap the live scorer.
    Use this to manually graduate a pre-trained challenger without triggering a retrain.
    """
    _authenticate(authorization)
    versions = await asyncio.to_thread(_model_registry.list_versions)
    known = {v.version for v in versions}
    if version not in known:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "VERSION_NOT_FOUND",
                               "message": f"Version '{version}' not found in registry. "
                                          f"Known versions: {sorted(known)}"}},
        )
    await asyncio.to_thread(_model_registry.promote, version)
    ok, msg = await asyncio.to_thread(_load_current_champion)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "PROMOTE_RELOAD_FAILED", "message": msg}},
        )
    log.info("model_promoted", version=version, triggered_by="analyst")
    return {"promoted": True, "version": version, "strategy": scorer.strategy_name}


class ABRequest(BaseModel):
    challenger_version: Optional[str] = None   # None = disable A/B
    traffic_pct:        float          = 0.3   # fraction to challenger (0.0–1.0)

@app.get("/v1/model/ab")
async def get_ab_routing(authorization: Optional[str] = Header(None, alias="Authorization")):
    """Return current A/B routing state."""
    _authenticate(authorization)
    return {
        "ab_enabled":         bool(_challenger_version),
        "challenger_version": _challenger_version or None,
        "traffic_pct":        scorer._challenger_pct if _challenger_version else 0.0,
    }


@app.post("/v1/model/ab")
async def set_ab_routing(
    body:          ABRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Enable or disable A/B champion/challenger routing at runtime without restart."""
    _authenticate(authorization)
    global _challenger_strategy, _challenger_version
    if not body.challenger_version:
        scorer.set_challenger(None, 0.0)
        _challenger_strategy = None
        _challenger_version  = ""
        return {"ab_enabled": False}

    versions = await asyncio.to_thread(_model_registry.list_versions)
    known = {v.version for v in versions}
    if body.challenger_version not in known:
        raise HTTPException(status_code=404,
            detail={"error": {"code": "VERSION_NOT_FOUND",
                               "message": f"Version '{body.challenger_version}' not found."}})
    pct = max(0.0, min(1.0, body.traffic_pct))
    try:
        chal = XGBoostStrategy(version=body.challenger_version)
        if not chal.is_ready():
            raise HTTPException(status_code=422,
                detail={"error": {"code": "CHALLENGER_NOT_READY",
                                   "message": f"Model {body.challenger_version} failed to load."}})
        scorer.set_challenger(chal, pct)
        _challenger_strategy = chal
        _challenger_version  = body.challenger_version
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"message": str(e)}})
    log.info("ab_routing_set", challenger=body.challenger_version, traffic_pct=pct)
    return {"ab_enabled": True, "challenger_version": body.challenger_version, "traffic_pct": pct}


@app.post("/v1/transactions/score", response_model=ScoreResponse)
@limiter.limit("60/minute")
async def score_transaction(
    request:          Request,
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

    # Score (run sync service in thread pool — does multiple DB reads/writes)
    fraud_score = await asyncio.to_thread(service.process, txn, dry_run=body.dry_run,
                                          serving_version=_serving_version,
                                          challenger_version=_challenger_version)

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
            "serving_version":   fraud_score.model_version,
            "ab_variant":        fraud_score.ab_variant,
            "latency_ms":        fraud_score.latency_ms,
            "scored_at":         fraud_score.scored_at.isoformat(),
        })

    # Emit Prometheus metrics
    record_score(
        decision   = fraud_score.decision.value,
        strategy   = scorer.strategy_name,
        score      = fraud_score.score,
        latency_ms = fraud_score.latency_ms,
        ab_variant = fraud_score.ab_variant,
    )

    # SLA warning
    if fraud_score.latency_ms > 200:
        print(f"  [WARN] SLA BREACH: {fraud_score.latency_ms}ms > 200ms")

    # Webhook delivery — fire-and-forget, does not block the response
    if config.WEBHOOK_URL and not body.dry_run:
        asyncio.create_task(_fire_webhook(response))

    return response


@app.post("/v1/transactions/score/batch", response_model=BatchScoreResponse)
@limiter.limit("10/minute")
async def score_batch(
    request:       Request,
    body:          BatchScoreRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Score up to 100 transactions in one call. Useful for backfill and import workflows.
    Each item in the response mirrors ScoreResponse; errors are per-item (never 5xx).
    Rate limit: 10 calls/minute per token (each call may contain up to 100 transactions).
    """
    _authenticate(authorization)

    results: list[BatchScoreItem] = []
    error_count = 0

    for txn_req in body.transactions:
        try:
            txn = Transaction(
                id          = txn_req.transaction_id,
                user_id     = txn_req.user_id,
                merchant_id = txn_req.merchant_id,
                device_id   = txn_req.device_id,
                amount      = txn_req.amount,
                currency    = txn_req.currency,
                channel     = Channel(txn_req.channel),
                ip_address  = txn_req.ip_address,
                created_at  = datetime.utcnow(),
            )
            fraud_score = await asyncio.to_thread(service.process, txn, dry_run=txn_req.dry_run, serving_version=_serving_version, challenger_version=_challenger_version)
            record_score(decision=fraud_score.decision.value, strategy=scorer.strategy_name,
                         score=fraud_score.score, latency_ms=fraud_score.latency_ms,
                         ab_variant=fraud_score.ab_variant)
            results.append(BatchScoreItem(
                transaction_id    = txn.id,
                fraud_probability = fraud_score.score,
                decision          = fraud_score.decision.value if hasattr(fraud_score.decision, "value") else str(fraud_score.decision),
                reason_codes      = fraud_score.reason_codes,
                model_version     = fraud_score.model_version,
                latency_ms        = fraud_score.latency_ms,
            ))
        except Exception as exc:
            error_count += 1
            results.append(BatchScoreItem(
                transaction_id = txn_req.transaction_id,
                error          = str(exc),
            ))

    return BatchScoreResponse(results=results, count=len(results) - error_count, errors=error_count)


@app.get("/v1/transactions/{transaction_id}/explain")
async def explain_transaction(
    transaction_id: str,
    authorization:  Optional[str] = Header(None, alias="Authorization"),
):
    """
    Return the stored fraud score and reason-code breakdown for a single transaction.
    Reason codes are pre-computed SHAP-derived signals saved at scoring time.
    """
    _authenticate(authorization)

    from sqlalchemy import select
    from fraudshield_core.db import transactions_table as _t, fraud_scores_table as _fs
    import json as _json

    def _fetch():
        with engine.connect() as conn:
            row = conn.execute(
                select(
                    _t.c.id.label("transaction_id"),
                    _t.c.user_id, _t.c.merchant_id, _t.c.amount, _t.c.currency, _t.c.channel,
                    _fs.c.score.label("fraud_probability"),
                    _fs.c.decision, _fs.c.reason_codes, _fs.c.strategy_used,
                    _fs.c.model_version, _fs.c.latency_ms, _fs.c.scored_at,
                )
                .select_from(_t.outerjoin(_fs, _t.c.id == _fs.c.transaction_id))
                .where(_t.c.id == transaction_id)
            ).fetchone()
        return row

    row = await asyncio.to_thread(_fetch)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TRANSACTION_NOT_FOUND",
                               "message": f"Transaction '{transaction_id}' not found"}},
        )

    raw_codes = row.reason_codes
    try:
        codes = json.loads(raw_codes) if isinstance(raw_codes, str) else (raw_codes or [])
    except Exception:
        codes = []

    return {
        "transaction_id":    row.transaction_id,
        "user_id":           row.user_id,
        "merchant_id":       row.merchant_id,
        "amount":            row.amount,
        "currency":          row.currency,
        "channel":           row.channel,
        "fraud_probability": row.fraud_probability,
        "decision":          row.decision,
        "model_version":     row.model_version,
        "strategy_used":     row.strategy_used,
        "latency_ms":        row.latency_ms,
        "scored_at":         str(row.scored_at) if row.scored_at else None,
        "reason_codes":      codes,
    }


@app.post("/v1/transactions/score/explain")
@limiter.limit("30/minute")
async def score_and_explain(
    request:       Request,
    body:          ScoreRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Dry-run score + inline SHAP reason codes in one call.
    Nothing is written to the database. Returns the full reason-code breakdown
    with float contributions so the UI can render contribution bars.

    Works with both XGBoost (real SHAP floats) and rule-based fallback (string codes).
    """
    _authenticate(authorization)

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

    # Always dry-run — no DB writes
    fraud_score = await asyncio.to_thread(service.process, txn, dry_run=True)

    # Normalise reason codes to list[{code, contribution}]
    raw = fraud_score.reason_codes
    if raw and isinstance(raw[0], str):
        # Rule-based strategy returns plain strings — wrap as contribution=1.0
        reason_codes = [{"code": rc, "contribution": 1.0} for rc in raw]
    else:
        reason_codes = raw

    return {
        "transaction_id":    txn.id,
        "fraud_probability": fraud_score.score,
        "decision":          fraud_score.decision.value,
        "model_version":     fraud_score.model_version,
        "strategy_used":     fraud_score.strategy_used if hasattr(fraud_score, "strategy_used") else scorer.strategy_name,
        "latency_ms":        fraud_score.latency_ms,
        "reason_codes":      reason_codes,
    }


@app.get("/v1/transactions", response_model=TransactionListResponse)
async def list_transactions(
    request:       Request,
    limit:         int = 20,
    order:         str = "desc",
    user_id:       Optional[str] = None,
    decision:      Optional[str] = None,
    cursor:        Optional[str] = None,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List recently scored transactions (for Live Feed & Investigate)."""
    _authenticate(authorization)
    items, next_cursor = await asyncio.to_thread(
        _query_transactions, limit, order, user_id, decision, cursor
    )
    return TransactionListResponse(
        items=[TransactionItem(**{**i, "scored_at": str(i["scored_at"]) if i.get("scored_at") else None}) for i in items],
        next_cursor=next_cursor,
        count=len(items),
    )


@app.get("/v1/dashboard/stats", response_model=StatsResponse)
async def dashboard_stats(
    request:       Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """24-hour scoring summary for Live Feed stat cards."""
    _authenticate(authorization)
    return await asyncio.to_thread(_query_stats)


@app.post("/v1/stream/token")
async def get_sse_token(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Exchange a permanent Bearer token for a short-lived (60 s) SSE URL token.
    Use the returned token as ?token= in the EventSource URL so the permanent
    API secret never appears in server access logs or browser history.
    """
    _authenticate(authorization)
    sse_token = str(uuid.uuid4())
    try:
        get_redis().setex(f"sse_token:{sse_token}", 60, "1")
    except Exception:
        pass  # Redis unavailable — stream endpoint falls back to permanent token check
    return {"token": sse_token, "expires_in": 60}


@app.get("/v1/stream")
async def stream_transactions(token: Optional[str] = None):
    """
    Server-Sent Events stream — one event per scored transaction.
    Auth via ?token= query param (EventSource cannot set headers).
    Prefer short-lived tokens issued by POST /v1/stream/token.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    token_valid = False
    try:
        redis = get_redis()
        # Short-lived token from Redis (preferred path)
        if redis.exists(f"sse_token:{token}"):
            token_valid = True
        else:
            # Permanent token fallback (e.g. Redis down, or direct API access)
            token_valid = (token == config.API_TOKEN)
    except Exception:
        # Redis unavailable — fall back to permanent token check
        token_valid = (token == config.API_TOKEN)

    if not token_valid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def generator():
        # ── Primary path: Redis async pub/sub (multi-replica) ──────────────
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(config.REDIS_URL, decode_responses=True)
            ps = r.pubsub()
            await ps.subscribe(CHANNEL)
            yield ": connected\n\n"
            loop = asyncio.get_running_loop()
            last_ka = loop.time()
            try:
                while True:
                    msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg["type"] == "message":
                        yield f"data: {msg['data']}\n\n"
                        last_ka = loop.time()
                    elif loop.time() - last_ka > 25:
                        yield ": keepalive\n\n"
                        last_ka = loop.time()
            except asyncio.CancelledError:
                pass
            finally:
                await ps.unsubscribe(CHANNEL)
                await r.aclose()
            return
        except Exception:
            pass

        # ── Fallback path: in-process queue (single replica / Redis down) ──
        q = broadcaster.subscribe_local()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unsubscribe_local(q)

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
    txn, score = await asyncio.gather(
        asyncio.to_thread(txn_repo.get_by_id, transaction_id),
        asyncio.to_thread(score_repo.get_by_transaction_id, transaction_id),
    )
    if not txn:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "RESOURCE_NOT_FOUND",
                               "message": f"Transaction {transaction_id} not found"}}
        )
    return {"transaction": txn, "score": score}


@app.get("/v1/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id:       str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Fetch user profile for Investigate page."""
    _authenticate(authorization)
    user = await asyncio.to_thread(user_repo.get_by_id, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "RESOURCE_NOT_FOUND",
                               "message": f"User {user_id} not found"}}
        )
    return UserResponse(
        id=user.id,
        email=user.email,
        phone=user.phone,
        risk_tier=user.risk_tier.value,
        kyc_status=user.kyc_status.value,
        created_at=user.created_at.isoformat() if user.created_at else "",
        updated_at=user.updated_at.isoformat() if user.updated_at else "",
    )


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
    await asyncio.to_thread(label_repo.save, label)
    return {"transaction_id": txn_id, "is_fraud": is_fraud, "saved": True}


@app.patch("/v1/users/{user_id}/risk-tier")
async def update_risk_tier(
    user_id:       str,
    body:          dict,
    request:       Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Analyst updates a user's risk tier manually. Change is logged with caller IP."""
    _authenticate(authorization)
    new_tier = body.get("risk_tier")
    if not new_tier:
        raise HTTPException(status_code=400,
            detail={"error": {"code": "MISSING_REQUIRED_FIELD",
                               "field": "risk_tier"}})
    caller_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)
    await asyncio.to_thread(
        user_repo.update_risk_tier,
        user_id,
        new_tier,
        "analyst",
        body.get("reason"),
        caller_ip,
    )
    return {"user_id": user_id, "risk_tier": new_tier, "updated": True}


@app.get("/v1/drift/report")
async def drift_report(
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Run PSI drift detection and return report.
    When DRIFT_AUTO_RETRAIN=true and recommendation is RETRAIN_REQUIRED,
    a retraining job is automatically queued in the background.
    """
    _authenticate(authorization)
    from ml_pipeline.monitoring.drift import run_drift_report
    report = await asyncio.to_thread(run_drift_report)
    if "error" in report:
        raise HTTPException(status_code=422, detail={"error": {"code": "DRIFT_ERROR", "message": report["error"]}})

    from fraud_api.metrics import update_psi_metrics
    update_psi_metrics(report.get("psi_by_feature", {}))

    if (config.DRIFT_AUTO_RETRAIN
            and report.get("recommendation") == "RETRAIN_REQUIRED"
            and not _retrain_in_progress.is_set()):
        _retrain_in_progress.set()
        log.info("drift_triggered_retrain", max_psi=report.get("max_psi"))
        background_tasks.add_task(_run_training_pipeline_guarded)
        report = {**report, "auto_retrain_queued": True}

    return report


@app.post("/v1/model/retrain", response_model=RetrainResponse, status_code=202)
async def trigger_retrain(
    body: RetrainRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Kick off a model retrain in the background.
    Returns 202 immediately; training runs asynchronously.
    Returns 409 if a retrain is already in progress.
    """
    _authenticate(authorization)
    if _retrain_in_progress.is_set():
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "RETRAIN_IN_PROGRESS",
                               "message": "A retraining job is already running"}},
        )
    _retrain_in_progress.set()
    log.info("retrain_requested", reason=body.reason, triggered_by="analyst")
    background_tasks.add_task(_run_training_pipeline_guarded)
    return RetrainResponse(
        status="started",
        message="Retraining pipeline enqueued. Monitor MLflow or GET /v1/model/versions for completion.",
        triggered_by="analyst",
    )


def _query_user_history(user_id: str) -> list:
    from fraudshield_core.db import user_risk_history_table as urh
    from sqlalchemy import select, desc
    with engine.connect() as conn:
        rows = conn.execute(
            select(urh).where(urh.c.user_id == user_id)
            .order_by(desc(urh.c.valid_from))
            .limit(50)
        ).fetchall()
    return [
        {
            "risk_tier":    r.risk_tier,
            "valid_from":   r.valid_from.isoformat() if hasattr(r.valid_from, "isoformat") else str(r.valid_from),
            "valid_to":     r.valid_to.isoformat() if r.valid_to and hasattr(r.valid_to, "isoformat") else (str(r.valid_to) if r.valid_to else None),
            "changed_by":   r.changed_by,
            "change_reason": r.change_reason,
            "caller_ip":    r.caller_ip,
        }
        for r in rows
    ]


@app.get("/v1/users/{user_id}/history")
async def user_risk_history(
    user_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Audit trail of risk tier changes for a user (SCD Type 2 history)."""
    _authenticate(authorization)
    history = await asyncio.to_thread(_query_user_history, user_id)
    return {"user_id": user_id, "history": history}


@app.get("/v1/model/card")
async def model_card(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Structured model card for the current champion: metrics, features, and thresholds."""
    _authenticate(authorization)
    try:
        _, meta = await asyncio.to_thread(_model_registry.load, "current")
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NO_CHAMPION", "message": "No champion model in registry"}},
        )
    return {
        "version":           meta.version,
        "trained_at":        meta.trained_at.isoformat(),
        "strategy":          scorer.strategy_name,
        "training_rows":     meta.training_rows,
        "fraud_rate":        meta.fraud_rate,
        "auc_roc":           meta.auc_roc,
        "precision":         meta.precision,
        "recall":            meta.recall,
        "f1_score":          meta.f1_score,
        "ks_statistic":      meta.ks_statistic,
        "calibration":       meta.calibration,
        "threshold":         meta.threshold,
        "features":          meta.feature_names,
        "fraud_threshold":   config.FRAUD_THRESHOLD,
        "review_threshold":  config.REVIEW_THRESHOLD,
    }


if __name__ == "__main__":
    uvicorn.run("fraud_api.main:app",
                host=config.API_HOST,
                port=config.API_PORT,
                reload=False,
                workers=1)
