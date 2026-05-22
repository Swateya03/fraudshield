"""
tests/factories.py
───────────────────
factory_boy + faker factories for building test fixtures.
Import and call the make_* helpers directly — no DB required.

Usage:
    from tests.factories import make_transaction, make_user, make_score

    txn  = make_transaction()                         # default sensible values
    txn2 = make_transaction(user_id="u_vip", amount=99_000.0, currency="USD")
    user = make_user(risk_tier=RiskTier.HIGH)
    score = make_score(decision="block", fraud_probability=0.92)
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import factory
from factory import fuzzy
from faker import Faker

from fraudshield_core.models import (
    Channel,
    KYCStatus,
    RiskTier,
    Transaction,
    User,
)

_fake = Faker("en_IN")
_fake.seed_instance(0)

_MERCHANTS  = ["m_grocery", "m_gas", "m_retail", "m_food", "m_crypto", "m_giftcard"]
_CURRENCIES = ["INR", "USD", "EUR"]
_CHANNELS   = list(Channel)


# ── Transaction ───────────────────────────────────────────────────────────────

class TransactionFactory(factory.Factory):
    class Meta:
        model = Transaction

    id          = factory.LazyFunction(lambda: f"txn_{_fake.uuid4()[:8]}")
    user_id     = factory.LazyFunction(lambda: f"u_{random.randint(1, 500):04d}")
    merchant_id = fuzzy.FuzzyChoice(_MERCHANTS)
    device_id   = factory.LazyFunction(lambda: f"dev_{random.randint(1, 200):04d}")
    amount      = fuzzy.FuzzyFloat(50.0, 25_000.0)
    currency    = fuzzy.FuzzyChoice(_CURRENCIES)
    channel     = fuzzy.FuzzyChoice(_CHANNELS)
    ip_address  = factory.LazyFunction(lambda: _fake.ipv4())
    created_at  = factory.LazyFunction(
        lambda: datetime.utcnow() - timedelta(seconds=random.randint(0, 86400))
    )


# ── User ──────────────────────────────────────────────────────────────────────

class UserFactory(factory.Factory):
    class Meta:
        model = User

    id         = factory.LazyFunction(lambda: f"u_{_fake.uuid4()[:8]}")
    email      = factory.LazyFunction(lambda: _fake.email())
    phone      = factory.LazyFunction(lambda: _fake.phone_number())
    risk_tier  = fuzzy.FuzzyChoice(list(RiskTier))
    kyc_status = fuzzy.FuzzyChoice(list(KYCStatus))
    created_at = factory.LazyFunction(
        lambda: datetime.utcnow() - timedelta(days=random.randint(1, 730))
    )
    updated_at = factory.LazyFunction(datetime.utcnow)


# ── Score response dict (not a domain model — just a plain dict) ──────────────

def make_score(
    *,
    transaction_id: str | None = None,
    fraud_probability: float | None = None,
    decision: str | None = None,
    reason_codes: list[str] | None = None,
    model_version: str = "v1.0.0",
    latency_ms: float | None = None,
) -> dict:
    """Build a score response dict matching the /v1/score API shape."""
    prob = fraud_probability if fraud_probability is not None else round(random.uniform(0, 1), 4)
    if decision is None:
        if prob >= 0.85:
            decision = "block"
        elif prob >= 0.50:
            decision = "review"
        else:
            decision = "allow"
    return {
        "transaction_id":    transaction_id or f"txn_{_fake.uuid4()[:8]}",
        "fraud_probability": prob,
        "decision":          decision,
        "reason_codes":      reason_codes or [],
        "model_version":     model_version,
        "latency_ms":        latency_ms or round(random.uniform(5, 45), 1),
    }


# ── Convenience helpers ───────────────────────────────────────────────────────

def make_transaction(**kwargs) -> Transaction:
    return TransactionFactory(**kwargs)


def make_user(**kwargs) -> User:
    return UserFactory(**kwargs)


def make_fraud_transaction(**kwargs) -> Transaction:
    """Return a transaction with high-fraud signals (crypto merchant, night hour, large amount)."""
    night = datetime.utcnow().replace(hour=2, minute=0, second=0)
    defaults = dict(
        merchant_id="m_crypto",
        amount=45_000.0,
        currency="INR",
        channel=Channel.ONLINE,
        ip_address="185.220.101.5",
        created_at=night,
    )
    defaults.update(kwargs)
    return TransactionFactory(**defaults)


def make_legit_transaction(**kwargs) -> Transaction:
    """Return a transaction with low-risk signals (grocery, daytime, small amount)."""
    day = datetime.utcnow().replace(hour=10, minute=0, second=0)
    defaults = dict(
        merchant_id="m_grocery",
        amount=350.0,
        currency="INR",
        channel=Channel.UPI,
        ip_address="103.28.50.10",
        created_at=day,
    )
    defaults.update(kwargs)
    return TransactionFactory(**defaults)
