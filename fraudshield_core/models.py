"""
fraudshield_core/models.py
────────────────
Pure domain models. No DB, no Redis, no framework.
These are the objects that flow through the entire system.

Scaling rule: these never change. They are the contract
between every layer of the system.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ─────────────────────────────────────────────
# Enums — enforced at domain level
# ─────────────────────────────────────────────

class RiskTier(str, Enum):
    UNKNOWN  = "unknown"
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    BLOCKED  = "blocked"


class KYCStatus(str, Enum):
    PENDING  = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Channel(str, Enum):
    ONLINE   = "online"
    POS      = "pos"
    ATM      = "atm"
    UPI      = "upi"
    NFC      = "nfc"


class Decision(str, Enum):
    ALLOW    = "allow"
    REVIEW   = "review"
    BLOCK    = "block"


class LabelSource(str, Enum):
    CHARGEBACK    = "chargeback"
    MANUAL_REVIEW = "manual_review"
    RULE          = "rule"
    MODEL         = "model"


class MerchantRisk(str, Enum):
    LOW      = "low"
    STANDARD = "standard"
    HIGH     = "high"
    BLOCKED  = "blocked"


# ─────────────────────────────────────────────
# Domain Models
# ─────────────────────────────────────────────

@dataclass
class User:
    """
    A registered user in the system.
    risk_tier and kyc_status change over time — see UserRiskHistory
    for point-in-time correct queries (SCD Type 2).
    """
    id:          str
    email:       str
    phone:       Optional[str]
    risk_tier:   RiskTier
    kyc_status:  KYCStatus
    created_at:  datetime
    updated_at:  datetime

    def is_blocked(self)    -> bool:
        return self.risk_tier == RiskTier.BLOCKED

    def is_high_risk(self)  -> bool:
        return self.risk_tier in (RiskTier.HIGH, RiskTier.BLOCKED)

    def is_new_user(self)   -> bool:
        delta = datetime.utcnow() - self.created_at
        return delta.days < 30


@dataclass
class Merchant:
    """
    A merchant where transactions occur.
    MCC = Merchant Category Code (4-digit ISO standard).
    """
    id:           str
    name:         str
    category:     str
    mcc:          str              # e.g. "5999" = electronics
    city:         Optional[str]
    state:        Optional[str]
    risk_level:   MerchantRisk
    registered_at: datetime


@dataclass
class Device:
    """
    Device fingerprint linked to a user.
    is_trusted = user has used this device many times before.
    """
    id:            str             # device fingerprint hash
    user_id:       str
    device_type:   str             # mobile / desktop / tablet
    os:            Optional[str]
    browser:       Optional[str]
    first_seen_at: datetime
    last_seen_at:  datetime
    is_trusted:    bool = False


@dataclass
class Transaction:
    """
    The core fact. Immutable once created.
    This is what the payment gateway sends us.
    """
    id:           str
    user_id:      str
    merchant_id:  str
    device_id:    Optional[str]
    amount:       float
    currency:     str
    channel:      Channel
    ip_address:   Optional[str]
    created_at:   datetime

    def is_large_amount(self, threshold: float = 10_000) -> bool:
        return self.amount > threshold

    def is_late_night(self) -> bool:
        return self.created_at.hour in range(0, 5)   # midnight to 5am


@dataclass
class FeatureVector:
    """
    The computed feature set passed to the ML model.
    Built by FeatureBuilder from a Transaction + context.

    Online features  → from Redis (real-time, sub-1ms)
    Offline features → from feature store (batch, updated daily)
    """
    transaction_id: str
    user_id:        str

    # ── Online features (real-time, from Redis) ──
    velocity_1h:    float = 0.0    # txn count last 1 hour
    velocity_6h:    float = 0.0    # txn count last 6 hours
    velocity_24h:   float = 0.0    # txn count last 24 hours
    amount_last_txn: float = 0.0   # previous transaction amount

    # ── Offline features (batch, from feature store) ──
    user_avg_amount:    float = 0.0   # historical average amount
    user_avg_velocity:  float = 0.0   # normal transactions per day
    device_trust_score: float = 0.0   # 0=new device, 1=trusted
    ip_fraud_history:   float = 0.0   # 0=clean, 1=known fraud IP
    user_age_days:      int   = 0     # days since account created
    merchant_risk_score: float = 0.0  # 0=low risk, 1=high risk
    hour_of_day:        int   = 0     # 0-23
    day_of_week:        int   = 0     # 0=Mon, 6=Sun

    # ── Derived features (computed from above) ──
    amount_ratio:       float = 0.0   # amount / user_avg_amount
    is_new_device:      int   = 0     # 1 if device never seen before
    is_late_night:      int   = 0     # 1 if hour in [0,1,2,3,4]
    is_new_user:        int   = 0     # 1 if account < 30 days old

    def to_model_input(self) -> list:
        """Returns ordered list for XGBoost input. Order MUST match training."""
        return [
            self.velocity_1h,
            self.velocity_6h,
            self.velocity_24h,
            self.amount_ratio,
            self.device_trust_score,
            self.ip_fraud_history,
            self.merchant_risk_score,
            self.hour_of_day,
            self.day_of_week,
            self.is_new_device,
            self.is_late_night,
            self.is_new_user,
        ]

    FEATURE_NAMES = [
        "velocity_1h", "velocity_6h", "velocity_24h",
        "amount_ratio", "device_trust_score", "ip_fraud_history",
        "merchant_risk_score", "hour_of_day", "day_of_week",
        "is_new_device", "is_late_night", "is_new_user",
    ]


@dataclass
class FraudScore:
    """
    The output of the fraud scoring pipeline.
    Stored in fraud_scores table.
    """
    id:              str
    transaction_id:  str
    score:           float          # 0.0 to 1.0
    decision:        Decision
    reason_codes:    List[dict]     # [{"code": "new_device", "contribution": 0.31}]
    model_version:   str
    strategy_used:   str
    latency_ms:      int
    scored_at:       datetime


@dataclass
class FraudLabel:
    """
    Ground truth — arrives ~30 days after transaction via chargeback.
    This is the feedback loop closing.
    """
    id:             str
    transaction_id: str
    is_fraud:       bool
    label_source:   LabelSource
    labeled_at:     datetime
    labeled_by:     Optional[str]  # analyst ID if manual review
    notes:          Optional[str]


@dataclass
class FraudEvent:
    """
    Published to the event bus after every scoring decision.
    Observers receive this — audit log, risk updater, dashboard.
    """
    transaction_id:  str
    user_id:         str
    merchant_id:     str
    amount:          float
    score:           float
    decision:        Decision
    reason_codes:    List[dict]
    model_version:   str
    latency_ms:      int
    scored_at:       datetime


@dataclass
class UserRiskHistory:
    """
    SCD Type 2 — full history of risk_tier changes.
    Used for point-in-time correct ML training features.

    valid_to = None means this is the CURRENT value.

    Query: what was risk_tier at transaction time?
    WHERE user_id = ? AND valid_from <= txn.created_at
      AND (valid_to IS NULL OR valid_to > txn.created_at)
    """
    id:            str
    user_id:       str
    risk_tier:     RiskTier
    valid_from:    datetime
    valid_to:      Optional[datetime]   # None = currently active
    changed_by:    str
    change_reason: Optional[str]


@dataclass
class ModelMetadata:
    """
    Metadata stored alongside each model version in the registry.
    """
    version:        str
    trained_at:     datetime
    training_rows:  int
    fraud_rate:     float
    auc_roc:        float
    precision:      float
    recall:         float
    f1_score:       float
    threshold:      float
    feature_names:  List[str]
    is_champion:    bool = False    # is this the currently deployed model?
