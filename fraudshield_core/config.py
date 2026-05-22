"""
fraudshield_core/config.py
──────────────────────────
All configuration loaded from environment variables.
Never hardcode anything. Every value has a sensible default for local dev.

Scaling rule: in production, these come from Kubernetes secrets
or AWS Parameter Store. The code never changes — only the env vars.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Repo root (parent of `fraudshield_core/`) — relative defaults must not depend on process cwd
# (e.g. Jupyter often uses notebooks/ and would otherwise resolve local_store/ there).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        return url
    path_part = url[len("sqlite:///") :]
    p = Path(path_part)
    if p.is_absolute():
        return url
    return f"sqlite:///{(_PROJECT_ROOT / path_part).as_posix()}"


def _parse_set(env_name: str, default: str) -> frozenset:
    """Parse a comma-separated env var into a frozenset of stripped strings."""
    raw = os.getenv(env_name, default)
    return frozenset(v.strip() for v in raw.split(",") if v.strip())


def _resolve_data_path(p: str) -> str:
    """Anchor relative paths to project root; leave http(s) and absolute paths unchanged."""
    p = p.strip()
    if p.startswith("http://") or p.startswith("https://"):
        return p
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str(_PROJECT_ROOT / p)


class Config:
    # ── Database ──────────────────────────────
    DB_URL: str           = _resolve_sqlite_url(os.getenv("DB_URL", "sqlite:///local_store/fraud.db"))

    # ── Redis ─────────────────────────────────
    REDIS_URL: str        = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_TTL_VELOCITY: int    = int(os.getenv("REDIS_TTL_VELOCITY", "3600"))
    REDIS_TTL_IDEMPOTENCY: int = int(os.getenv("REDIS_TTL_IDEMPOTENCY", "86400"))

    # ── API ───────────────────────────────────
    API_HOST: str         = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int         = int(os.getenv("API_PORT", "8000"))
    API_TOKEN: str        = os.getenv("API_TOKEN", "dev_token_fraudshield_local_only")
    RATE_LIMIT_PER_SECOND: int = int(os.getenv("RATE_LIMIT_PER_SECOND", "100"))
    # Comma-separated list of allowed origins for CORS.
    # In production set this to your dashboard domain, e.g. https://fraud.yourcompany.com
    CORS_ORIGINS: list    = [
        o.strip() for o in
        os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
        if o.strip()
    ]

    # ── Model ─────────────────────────────────
    MODEL_REGISTRY_PATH: str   = _resolve_data_path(os.getenv("MODEL_REGISTRY_PATH", "local_store/model_registry"))
    CURRENT_MODEL_VERSION: str = os.getenv("CURRENT_MODEL_VERSION", "v1.0.0")
    FRAUD_THRESHOLD: float     = float(os.getenv("FRAUD_THRESHOLD", "0.85"))
    REVIEW_THRESHOLD: float    = float(os.getenv("REVIEW_THRESHOLD", "0.50"))

    # ── Feature Store ─────────────────────────
    FEATURE_STORE_BACKEND: str = os.getenv("FEATURE_STORE_BACKEND", "redis")

    # ── MLflow ────────────────────────────────
    MLFLOW_TRACKING_URI: str   = _resolve_data_path(os.getenv("MLFLOW_TRACKING_URI", "local_store/mlruns"))

    # ── Training ───────────────────────────────
    USE_PU_LEARNING: bool  = os.getenv("USE_PU_LEARNING", "false").lower() == "true"

    # ── Champion / Challenger A/B routing ──────
    # Set CHALLENGER_MODEL_VERSION to an existing registry version +
    # CHALLENGER_TRAFFIC_PCT to a float in [0.0, 1.0] (e.g. 0.10 = 10%).
    # Leave CHALLENGER_MODEL_VERSION empty (default) to disable A/B routing.
    CHALLENGER_MODEL_VERSION: str   = os.getenv("CHALLENGER_MODEL_VERSION", "")
    CHALLENGER_TRAFFIC_PCT:   float = float(os.getenv("CHALLENGER_TRAFFIC_PCT", "0.0"))

    # ── Fraud Signal Lists ────────────────────
    FRAUD_IP_LIST: frozenset = _parse_set(
        "FRAUD_IP_LIST",
        "185.220.101.5,185.220.101.6,192.42.116.16,199.87.154.255,23.129.64.131",
    )
    HIGH_RISK_MERCHANT_IDS: frozenset = _parse_set(
        "HIGH_RISK_MERCHANT_IDS",
        "m_crypto,m_giftcard,m_jewelry,m_luxury",
    )
    MEDIUM_RISK_MERCHANT_IDS: frozenset = _parse_set(
        "MEDIUM_RISK_MERCHANT_IDS",
        "m_bestbuy,m_electronics",
    )
    HIGH_RISK_MERCHANT_CATEGORIES: frozenset = _parse_set(
        "HIGH_RISK_MERCHANT_CATEGORIES",
        "crypto,gift_cards,gambling,wire_transfer",
    )
    MEDIUM_RISK_MERCHANT_CATEGORIES: frozenset = _parse_set(
        "MEDIUM_RISK_MERCHANT_CATEGORIES",
        "jewelry,electronics,luxury",
    )

    # ── Currency normalisation (all amounts converted to INR before feature computation) ──
    EXCHANGE_RATES_TO_INR: dict = {
        "INR": float(os.getenv("FX_INR", "1.0")),
        "USD": float(os.getenv("FX_USD", "83.0")),
        "EUR": float(os.getenv("FX_EUR", "90.0")),
        "GBP": float(os.getenv("FX_GBP", "105.0")),
    }

    # ── Drift Detection ───────────────────────
    PSI_THRESHOLD: float       = float(os.getenv("PSI_THRESHOLD", "0.20"))
    DRIFT_CHECK_WINDOW_DAYS: int = int(os.getenv("DRIFT_CHECK_WINDOW_DAYS", "7"))
    # Automatically trigger retraining when drift report returns RETRAIN_REQUIRED.
    DRIFT_AUTO_RETRAIN: bool   = os.getenv("DRIFT_AUTO_RETRAIN", "false").lower() == "true"

    # ── A/B Champion/Challenger auto-promotion ─
    # Challenger is auto-promoted when its AUC exceeds the champion's by at least this margin.
    AB_PROMOTE_THRESHOLD: float = float(os.getenv("AB_PROMOTE_THRESHOLD", "0.01"))

    # ── Event Publisher ───────────────────────
    EVENT_PUBLISHER_BACKEND: str = os.getenv("EVENT_PUBLISHER_BACKEND", "memory")
    KAFKA_BROKERS: str           = os.getenv("KAFKA_BROKERS", "localhost:9092")
    KAFKA_TOPIC_SCORED: str      = os.getenv("KAFKA_TOPIC_SCORED", "scored_transactions")

    # ── Webhook ───────────────────────────────
    # Fire a POST to WEBHOOK_URL for each decision in WEBHOOK_EVENTS.
    # WEBHOOK_EVENTS: comma-separated subset of "block,review,allow" (default: block only)
    WEBHOOK_URL:    str       = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_EVENTS: frozenset = _parse_set("WEBHOOK_EVENTS", "block")

    # ── Logging ───────────────────────────────
    LOG_LEVEL: str        = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str       = os.getenv("LOG_FORMAT", "json")


# Single instance — import this everywhere
config = Config()
