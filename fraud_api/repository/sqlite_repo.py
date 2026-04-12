"""
fraud_api/repository/sqlite_repo.py
─────────────────────────────────────
SQLite implementation of all repositories.

MVP storage backend. When you're ready for production,
write PostgreSQLUserRepository with the same interface.
FraudAPI never changes.

Note: SQLAlchemy Core used throughout — explicit SQL,
no ORM magic. You know exactly what query runs.
"""

from sqlalchemy import select, insert, update, and_, func, text
from sqlalchemy.engine import Engine
from datetime import datetime, timedelta
from typing import Optional, List
import json
import uuid

from fraudshield_core.models import (
    User, Transaction, FraudScore, FraudLabel, UserRiskHistory,
    Merchant, MerchantRisk,
    RiskTier, KYCStatus, Channel, Decision, LabelSource
)
from fraudshield_core.db import (
    get_engine,
    users_table, merchants_table, transactions_table, fraud_scores_table,
    fraud_labels_table, user_risk_history_table, devices_table
)
from fraud_api.repository.base import (
    UserRepository, TransactionRepository,
    FraudScoreRepository, FraudLabelRepository,
    MerchantRepository,
)


class SQLiteUserRepository(UserRepository):

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def get_by_id(self, user_id: str) -> Optional[User]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users_table).where(users_table.c.id == user_id)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users_table).where(users_table.c.email == email)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def save(self, user: User) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(users_table.c.id).where(users_table.c.id == user.id)
            ).fetchone()
            if existing:
                conn.execute(
                    update(users_table)
                    .where(users_table.c.id == user.id)
                    .values(
                        email      = user.email,
                        phone      = user.phone,
                        risk_tier  = user.risk_tier.value,
                        kyc_status = user.kyc_status.value,
                        updated_at = datetime.utcnow(),
                    )
                )
            else:
                conn.execute(
                    insert(users_table).values(
                        id         = user.id,
                        email      = user.email,
                        phone      = user.phone,
                        risk_tier  = user.risk_tier.value,
                        kyc_status = user.kyc_status.value,
                        created_at = user.created_at,
                        updated_at = user.updated_at,
                    )
                )

    def update_risk_tier(self, user_id: str, risk_tier: str,
                         changed_by: str = "system",
                         reason: str = None) -> None:
        now = datetime.utcnow()
        with self.engine.begin() as conn:
            # 1. Close current history record
            conn.execute(
                update(user_risk_history_table)
                .where(
                    and_(
                        user_risk_history_table.c.user_id == user_id,
                        user_risk_history_table.c.valid_to.is_(None)
                    )
                )
                .values(valid_to=now)
            )
            # 2. Insert new history record (SCD Type 2)
            conn.execute(
                insert(user_risk_history_table).values(
                    id            = str(uuid.uuid4()),
                    user_id       = user_id,
                    risk_tier     = risk_tier,
                    valid_from    = now,
                    valid_to      = None,
                    changed_by    = changed_by,
                    change_reason = reason,
                )
            )
            # 3. Update current user record
            conn.execute(
                update(users_table)
                .where(users_table.c.id == user_id)
                .values(risk_tier=risk_tier, updated_at=now)
            )

    def get_risk_tier_at(self, user_id: str,
                          at_time: datetime) -> Optional[str]:
        """Point-in-time correct query — avoids temporal leakage in training."""
        with self.engine.connect() as conn:
            row = conn.execute(
                select(user_risk_history_table.c.risk_tier)
                .where(
                    and_(
                        user_risk_history_table.c.user_id == user_id,
                        user_risk_history_table.c.valid_from <= at_time,
                        (user_risk_history_table.c.valid_to.is_(None)) |
                        (user_risk_history_table.c.valid_to > at_time)
                    )
                )
                .order_by(user_risk_history_table.c.valid_from.desc())
                .limit(1)
            ).fetchone()
        return row[0] if row else None

    def _row_to_user(self, row) -> User:
        return User(
            id         = row.id,
            email      = row.email,
            phone      = row.phone,
            risk_tier  = RiskTier(row.risk_tier),
            kyc_status = KYCStatus(row.kyc_status),
            created_at = row.created_at,
            updated_at = row.updated_at,
        )


class SQLiteTransactionRepository(TransactionRepository):

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def save(self, transaction: Transaction) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(transactions_table).prefix_with("OR IGNORE").values(
                    id          = transaction.id,
                    user_id     = transaction.user_id,
                    merchant_id = transaction.merchant_id,
                    device_id   = transaction.device_id,
                    amount      = transaction.amount,
                    currency    = transaction.currency,
                    channel     = transaction.channel.value,
                    ip_address  = transaction.ip_address,
                    created_at  = transaction.created_at,
                )
            )

    def get_by_id(self, transaction_id: str) -> Optional[Transaction]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(transactions_table)
                .where(transactions_table.c.id == transaction_id)
            ).fetchone()
        return self._row_to_txn(row) if row else None

    def get_recent_by_user(self, user_id: str,
                            limit: int = 20) -> List[Transaction]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(transactions_table)
                .where(transactions_table.c.user_id == user_id)
                .order_by(transactions_table.c.created_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_txn(r) for r in rows]

    def get_velocity(self, user_id: str, window_hours: int) -> int:
        """Uses idx_txns_user_time index — fast even at 100M rows."""
        cutoff = datetime.utcnow() - timedelta(hours=window_hours)
        with self.engine.connect() as conn:
            result = conn.execute(
                select(func.count())
                .select_from(transactions_table)
                .where(
                    and_(
                        transactions_table.c.user_id == user_id,
                        transactions_table.c.created_at >= cutoff
                    )
                )
            ).scalar()
        return result or 0

    def _row_to_txn(self, row) -> Transaction:
        return Transaction(
            id          = row.id,
            user_id     = row.user_id,
            merchant_id = row.merchant_id,
            device_id   = row.device_id,
            amount      = row.amount,
            currency    = row.currency,
            channel     = Channel(row.channel),
            ip_address  = row.ip_address,
            created_at  = row.created_at,
        )


class SQLiteFraudScoreRepository(FraudScoreRepository):

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def save(self, score: FraudScore) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(fraud_scores_table).values(
                    id             = score.id,
                    transaction_id = score.transaction_id,
                    score          = score.score,
                    decision       = score.decision.value,
                    reason_codes   = json.dumps(score.reason_codes),
                    model_version  = score.model_version,
                    strategy_used  = score.strategy_used,
                    latency_ms     = score.latency_ms,
                    scored_at      = score.scored_at,
                )
            )

    def get_by_transaction_id(self, transaction_id: str) -> Optional[FraudScore]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(fraud_scores_table)
                .where(fraud_scores_table.c.transaction_id == transaction_id)
            ).fetchone()
        if not row:
            return None
        return FraudScore(
            id             = row.id,
            transaction_id = row.transaction_id,
            score          = row.score,
            decision       = Decision(row.decision),
            reason_codes   = json.loads(row.reason_codes),
            model_version  = row.model_version,
            strategy_used  = row.strategy_used,
            latency_ms     = row.latency_ms,
            scored_at      = row.scored_at,
        )


class SQLiteFraudLabelRepository(FraudLabelRepository):

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def save(self, label: FraudLabel) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(fraud_labels_table).values(
                    id             = label.id,
                    transaction_id = label.transaction_id,
                    is_fraud       = label.is_fraud,
                    label_source   = label.label_source.value,
                    labeled_at     = label.labeled_at,
                    labeled_by     = label.labeled_by,
                    notes          = label.notes,
                )
            )

    def get_by_transaction_id(self, transaction_id: str) -> Optional[FraudLabel]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(fraud_labels_table)
                .where(fraud_labels_table.c.transaction_id == transaction_id)
            ).fetchone()
        if not row:
            return None
        return FraudLabel(
            id             = row.id,
            transaction_id = row.transaction_id,
            is_fraud       = bool(row.is_fraud),
            label_source   = LabelSource(row.label_source),
            labeled_at     = row.labeled_at,
            labeled_by     = row.labeled_by,
            notes          = row.notes,
        )

    def get_unlabeled_reviews(self, limit: int = 50) -> List[dict]:
        """Transactions scored as 'review' with no label yet — analyst queue."""
        query = text("""
            SELECT t.id, t.user_id, t.amount, t.channel,
                   t.created_at, fs.score, fs.reason_codes
            FROM transactions t
            JOIN fraud_scores fs ON fs.transaction_id = t.id
            LEFT JOIN fraud_labels fl ON fl.transaction_id = t.id
            WHERE fs.decision = 'review'
              AND fl.id IS NULL
            ORDER BY t.created_at DESC
            LIMIT :limit
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(query, {"limit": limit}).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_labeled_training_data(self,  # noqa: C901
                                   from_date: datetime,
                                   to_date: datetime) -> List[dict]:
        """
        Training dataset query.
        Returns transactions with features + labels for model training.
        Uses point-in-time risk tier from user_risk_history (no leakage).
        """
        query = text("""
            SELECT
                t.id            AS transaction_id,
                t.user_id,
                t.merchant_id,
                t.amount,
                t.channel,
                t.ip_address,
                t.created_at,
                fs.score        AS model_score,
                fs.model_version,
                fl.is_fraud     AS label
            FROM transactions t
            JOIN fraud_scores  fs ON fs.transaction_id = t.id
            JOIN fraud_labels  fl ON fl.transaction_id = t.id
            WHERE t.created_at BETWEEN :from_date AND :to_date
            ORDER BY t.created_at
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(query, {
                "from_date": from_date,
                "to_date":   to_date
            }).fetchall()
        return [dict(r._mapping) for r in rows]


class SQLiteMerchantRepository(MerchantRepository):

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def get_by_id(self, merchant_id: str) -> Optional[Merchant]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(merchants_table).where(merchants_table.c.id == merchant_id)
            ).fetchone()
        if not row:
            return None
        return Merchant(
            id            = row.id,
            name          = row.name,
            category      = row.category,
            mcc           = row.mcc,
            city          = row.city,
            state         = row.state,
            risk_level    = MerchantRisk(row.risk_level),
            registered_at = row.registered_at,
        )
