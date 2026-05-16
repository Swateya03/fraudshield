"""
ml_pipeline/training/dataset.py
─────────────────────────────────
Pulls labeled training data and builds feature matrix.
Point-in-time correct features (no temporal leakage).
"""

from datetime import datetime, timedelta
from sqlalchemy import text
from typing import Tuple
import math
import pandas as pd
import numpy as np

from fraudshield_core.db import get_engine
from fraudshield_core.models import FeatureVector


def _compute_velocity(group: pd.DataFrame, col: str, windows: dict) -> pd.DataFrame:
    """Fast rolling-count velocity per user using numpy searchsorted."""
    group = group.sort_values(col)
    ts_values = group[col].values.astype("int64")
    for label, window in windows.items():
        window_ns = int(window.total_seconds() * 1e9)
        starts = ts_values - window_ns
        left  = np.searchsorted(ts_values, starts, side="left")
        right = np.arange(1, len(ts_values) + 1)
        group[label] = right - left
    return group


def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Shared feature computation for both labeled and full datasets."""

    # ── Velocity features ──────────────────────────────────────
    df["_ts"] = pd.to_datetime(df["created_at"])
    velocity_windows = {
        "velocity_1h":  timedelta(hours=1),
        "velocity_6h":  timedelta(hours=6),
        "velocity_24h": timedelta(hours=24),
    }
    df = (
        df.groupby("user_id", group_keys=False)
          .apply(lambda g: _compute_velocity(g, "_ts", velocity_windows))
    )

    # ── User average amount (point-in-time: only prior txns) ──
    df = df.sort_values("_ts")
    df["user_avg_amount_historical"] = (
        df.groupby("user_id")["amount"]
          .transform(lambda s: s.expanding().mean().shift(1))
    )

    # ── User std amount (point-in-time) ───────────────────────
    df["user_std_historical"] = (
        df.groupby("user_id")["amount"]
          .transform(lambda s: s.expanding().std().shift(1))
    )

    # ── Device features ────────────────────────────────────────
    device_first = pd.to_datetime(df["device_first_seen"])
    df["device_age_days"] = (df["_ts"] - device_first).dt.days

    # ── Currency normalisation → INR equivalent for all amount features ──
    from fraudshield_core.config import config as _cfg
    if "currency" in df.columns:
        df["_amount_inr"] = df.apply(
            lambda r: r["amount"] * _cfg.EXCHANGE_RATES_TO_INR.get(r["currency"], 1.0), axis=1
        )
    else:
        df["_amount_inr"] = df["amount"]  # training data pre-dates currency column → all INR

    # ── Derived features ───────────────────────────────────────
    avg_hist = df["user_avg_amount_historical"].fillna(500).clip(lower=1)
    std_hist = df["user_std_historical"].fillna(1).clip(lower=1)

    df["amount_ratio"]       = df["_amount_inr"] / avg_hist
    df["is_new_device"]      = ((df["device_age_days"].isna()) | (df["device_age_days"] < 1)).astype(int)
    df["device_trust_score"] = (df["device_age_days"].fillna(0) / 30).clip(upper=1.0)
    df["is_late_night"]      = (df["_ts"].dt.hour < 5).astype(int)
    df["hour_of_day"]        = df["_ts"].dt.hour
    df["day_of_week"]        = df["_ts"].dt.dayofweek
    df["ip_fraud_history"]   = df["ip_address"].isin(_cfg.FRAUD_IP_LIST).astype(float)
    df["merchant_risk_score"] = df["merchant_id"].apply(
        lambda x: 1.0 if x in _cfg.HIGH_RISK_MERCHANT_IDS else 0.1
    )
    df["is_new_user"]       = 0

    # ── amount_zscore & geo_mismatch ──────────────────────────
    df["amount_zscore"] = (df["_amount_inr"] - avg_hist) / std_hist
    df["geo_mismatch"]  = (
        df["user_city"].fillna("").str.lower() != df["merchant_city"].fillna("").str.lower()
    ).astype(int)

    # ── Day 5 features ────────────────────────────────────────
    df["log_amount"]       = df["_amount_inr"].apply(lambda x: math.log1p(x))
    df["is_round_amount"]  = df["_amount_inr"].apply(lambda x: int(x == int(x) and x % 100 == 0))
    df["is_weekend"]       = (df["_ts"].dt.dayofweek >= 5).astype(int)

    user_updated = pd.to_datetime(df["user_updated_at"])
    df["hours_since_profile_update"] = (
        (df["_ts"] - user_updated).dt.total_seconds() / 3600.0
    ).clip(lower=0).fillna(9999)

    merch_reg = pd.to_datetime(df["merchant_registered_at"])
    df["merchant_tenure_days"] = (df["_ts"] - merch_reg).dt.days.clip(lower=0).fillna(0).astype(int)

    # ── Day 6: time_since_last_txn ────────────────────────────
    df = df.sort_values(["user_id", "_ts"])
    df["_prev_ts"] = df.groupby("user_id")["_ts"].shift(1)
    df["time_since_last_txn_secs"] = (
        (df["_ts"] - df["_prev_ts"]).dt.total_seconds().fillna(86400.0)
    )

    # ── Channel risk ───────────────────────────────────────────
    from fraudshield_core.models import CHANNEL_RISK
    df["channel_risk"] = df["channel"].map(CHANNEL_RISK).fillna(0.5)

    return df


_BASE_SQL = """
    SELECT
        t.id             AS transaction_id,
        t.user_id,
        t.amount,
        t.channel,
        t.ip_address,
        t.device_id,
        t.created_at,
        t.merchant_id,
        d.first_seen_at  AS device_first_seen,
        d.is_trusted     AS device_is_trusted,
        u.city           AS user_city,
        u.updated_at     AS user_updated_at,
        m.city           AS merchant_city,
        m.registered_at  AS merchant_registered_at
"""


def build_training_dataset(from_date: datetime = None,
                            to_date:   datetime = None) -> Tuple[pd.DataFrame, pd.Series, np.ndarray]:
    """
    Builds feature matrix X, label vector y, and user groups for GroupShuffleSplit.
    Point-in-time correct features (no temporal leakage).
    """
    engine = get_engine()
    if to_date   is None: to_date   = datetime.utcnow()
    if from_date is None: from_date = to_date - timedelta(days=90)

    query = text(_BASE_SQL + """,
        fl.is_fraud
    FROM transactions t
    JOIN fraud_labels fl ON fl.transaction_id = t.id
    LEFT JOIN devices d  ON d.id = t.device_id
    LEFT JOIN users u    ON u.id = t.user_id
    LEFT JOIN merchants m ON m.id = t.merchant_id
    WHERE t.created_at BETWEEN :from_date AND :to_date
    ORDER BY t.created_at
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={
            "from_date": from_date.isoformat(),
            "to_date":   to_date.isoformat(),
        })

    if df.empty:
        raise ValueError("No labeled training data found. Run seed_data.py first.")

    df = _compute_features(df)

    feature_cols = FeatureVector.FEATURE_NAMES
    X = df[feature_cols].fillna(0).astype(float)
    y = df["is_fraud"].astype(int)
    groups = df["user_id"].values

    print(f"  Dataset: {len(df):,} rows | "
          f"Fraud: {y.sum():,} ({y.mean()*100:.1f}%) | "
          f"Features: {len(feature_cols)}")

    return X, y, groups


def build_full_dataset(from_date: datetime = None,
                       to_date:   datetime = None) -> Tuple[pd.DataFrame, pd.Series, np.ndarray, np.ndarray]:
    """
    Returns all transactions (labeled + unlabeled) for PU Learning.
    Returns (X, y_partial, groups, is_labeled).
    y_partial = 1 for fraud, 0 for legit/unlabeled.
    is_labeled = True where a fraud_labels row exists.
    """
    engine = get_engine()
    if to_date   is None: to_date   = datetime.utcnow()
    if from_date is None: from_date = to_date - timedelta(days=90)

    query = text(_BASE_SQL + """,
        fl.is_fraud,
        CASE WHEN fl.id IS NOT NULL THEN 1 ELSE 0 END AS is_labeled
    FROM transactions t
    LEFT JOIN fraud_labels fl ON fl.transaction_id = t.id
    LEFT JOIN devices d  ON d.id = t.device_id
    LEFT JOIN users u    ON u.id = t.user_id
    LEFT JOIN merchants m ON m.id = t.merchant_id
    WHERE t.created_at BETWEEN :from_date AND :to_date
    ORDER BY t.created_at
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={
            "from_date": from_date.isoformat(),
            "to_date":   to_date.isoformat(),
        })

    if df.empty:
        raise ValueError("No transaction data found. Run seed_data.py first.")

    df["is_fraud"] = df["is_fraud"].fillna(0)
    df = _compute_features(df)

    feature_cols = FeatureVector.FEATURE_NAMES
    X = df[feature_cols].fillna(0).astype(float)
    y_partial  = df["is_fraud"].astype(int)
    groups     = df["user_id"].values
    is_labeled = df["is_labeled"].astype(bool).values

    print(f"  Full dataset: {len(df):,} rows | "
          f"Labeled: {is_labeled.sum():,} ({is_labeled.mean()*100:.1f}%) | "
          f"Features: {len(feature_cols)}")

    return X, y_partial, groups, is_labeled
