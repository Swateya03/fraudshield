"""
fraudshield_core/db.py
────────────
Database connection and table definitions.

MVP:        SQLite (file on disk, no server)
Production: swap DB_URL to PostgreSQL — zero code change here

We use SQLAlchemy Core (not ORM) — gives SQL control
without magic, while keeping the swap-ability.
"""

from sqlalchemy import (
    create_engine, MetaData, Table, Column,
    String, Float, Integer, Boolean, DateTime, Text,
    Index, text
)
from sqlalchemy.engine import Engine
from fraudshield_core.config import config
import os

# ── Engine ────────────────────────────────────────────────────
# SQLite needs check_same_thread=False for FastAPI's threading
# PostgreSQL doesn't need this — it's ignored automatically
connect_args = {"check_same_thread": False} if "sqlite" in config.DB_URL else {}

# For SQLite: create the parent directory before the engine is instantiated.
# SQLite cannot create a DB file when the parent directory doesn't exist, and
# local_store/ is gitignored so it's absent in fresh CI checkouts.
if "sqlite" in config.DB_URL:
    _db_path = config.DB_URL.split("sqlite:///", 1)[-1]
    _db_dir = os.path.dirname(_db_path)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)

_is_sqlite = "sqlite" in config.DB_URL

# SQLite uses StaticPool (single connection, no pool overhead).
# PostgreSQL gets explicit pool settings tuned for the API's async-to-thread model:
#   pool_size=10  — baseline connections kept alive
#   max_overflow=20 — burst headroom (total max = 30)
#   pool_timeout=10 — raise after 10s if no connection available (fail fast)
#   pool_recycle=1800 — recycle connections every 30 min (avoids stale TCP)
if _is_sqlite:
    from sqlalchemy.pool import NullPool
    _pool_kwargs: dict = {"poolclass": NullPool}
else:
    _pool_kwargs = {
        "pool_size":    int(os.getenv("DB_POOL_SIZE",    "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "10")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
    }

engine: Engine = create_engine(
    config.DB_URL,
    connect_args=connect_args,
    echo=False,          # set True to see SQL in terminal (debugging)
    pool_pre_ping=True,  # check connection health before using
    **_pool_kwargs,
)

if "sqlite" in config.DB_URL:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")    # 64 MB
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

metadata = MetaData()


# ── Table Definitions ─────────────────────────────────────────
# These match your schema design from slides 50-55 exactly.

users_table = Table("users", metadata,
    Column("id",          String,   primary_key=True),
    Column("email",       String,   nullable=False, unique=True),
    Column("phone",       String),
    Column("risk_tier",   String,   nullable=False, default="unknown"),
    Column("kyc_status",  String,   nullable=False, default="pending"),
    Column("city",        String),
    Column("state",       String),
    Column("created_at",  DateTime, nullable=False),
    Column("updated_at",  DateTime, nullable=False),
)

merchants_table = Table("merchants", metadata,
    Column("id",           String,  primary_key=True),
    Column("name",         String,  nullable=False),
    Column("category",     String,  nullable=False),
    Column("mcc",          String,  nullable=False),
    Column("city",         String),
    Column("state",        String),
    Column("risk_level",   String,  nullable=False, default="standard"),
    Column("registered_at",DateTime,nullable=False),
)

devices_table = Table("devices", metadata,
    Column("id",            String,  primary_key=True),  # device fingerprint
    Column("user_id",       String,  nullable=False),
    Column("device_type",   String,  nullable=False),
    Column("os",            String),
    Column("browser",       String),
    Column("first_seen_at", DateTime,nullable=False),
    Column("last_seen_at",  DateTime,nullable=False),
    Column("is_trusted",    Boolean, nullable=False, default=False),
)

transactions_table = Table("transactions", metadata,
    Column("id",          String,  primary_key=True),
    Column("user_id",     String,  nullable=False),
    Column("merchant_id", String,  nullable=False),
    Column("device_id",   String),
    Column("amount",      Float,   nullable=False),
    Column("currency",    String,  nullable=False, default="INR"),
    Column("channel",     String,  nullable=False),
    Column("ip_address",  String),
    Column("created_at",  DateTime,nullable=False),
    Column("txn_source",  String,  default="prod"),
)

fraud_scores_table = Table("fraud_scores", metadata,
    Column("id",             String,  primary_key=True),
    Column("transaction_id", String,  nullable=False, unique=True),
    Column("score",          Float,   nullable=False),
    Column("decision",       String,  nullable=False),
    Column("reason_codes",   Text,    nullable=False),  # JSON string
    Column("model_version",  String,  nullable=False),
    Column("strategy_used",  String,  nullable=False),
    Column("latency_ms",     Integer),
    Column("scored_at",      DateTime,nullable=False),
    Column("ab_variant",     String,  nullable=True, default="champion"),
)

fraud_labels_table = Table("fraud_labels", metadata,
    Column("id",             String,  primary_key=True),
    Column("transaction_id", String,  nullable=False, unique=True),
    Column("is_fraud",       Boolean, nullable=False),
    Column("label_source",   String,  nullable=False),
    Column("labeled_at",     DateTime,nullable=False),
    Column("labeled_by",     String),
    Column("notes",          Text),
)

# SCD Type 2 — full history of user risk tier changes
user_risk_history_table = Table("user_risk_history", metadata,
    Column("id",            String,  primary_key=True),
    Column("user_id",       String,  nullable=False),
    Column("risk_tier",     String,  nullable=False),
    Column("valid_from",    DateTime,nullable=False),
    Column("valid_to",      DateTime),                  # NULL = currently active
    Column("changed_by",    String,  nullable=False),
    Column("change_reason", Text),
    Column("caller_ip",     String),                    # IP of the API caller
)


# ── Indexes ───────────────────────────────────────────────────
# From your schema design in slides 53-54:
# Every column you filter on in WHERE clauses needs an index.

Index("idx_txns_user_time",  transactions_table.c.user_id,
                             transactions_table.c.created_at)
Index("idx_txns_ip",         transactions_table.c.ip_address,
                             transactions_table.c.created_at)
Index("idx_txns_merchant",   transactions_table.c.merchant_id,
                             transactions_table.c.created_at)
Index("idx_scores_txn",      fraud_scores_table.c.transaction_id)
Index("idx_labels_txn",      fraud_labels_table.c.transaction_id)
Index("idx_devices_user",    devices_table.c.user_id)
Index("idx_risk_hist_user",  user_risk_history_table.c.user_id,
                             user_risk_history_table.c.valid_from)


def _run_migrations() -> None:
    """Add columns introduced after the initial schema. Safe to run on every startup."""
    with engine.begin() as conn:
        existing_rh = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(user_risk_history)")).fetchall()
        }
        if "caller_ip" not in existing_rh:
            conn.execute(text("ALTER TABLE user_risk_history ADD COLUMN caller_ip TEXT"))

        existing_fs = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(fraud_scores)")).fetchall()
        }
        if "ab_variant" not in existing_fs:
            conn.execute(text("ALTER TABLE fraud_scores ADD COLUMN ab_variant TEXT DEFAULT 'champion'"))


def create_all_tables() -> None:
    """Create all tables. Safe to call multiple times (CREATE IF NOT EXISTS)."""
    metadata.create_all(engine)
    if "sqlite" in config.DB_URL:
        _run_migrations()
    print("[ok] All tables created")


def get_engine() -> Engine:
    return engine
