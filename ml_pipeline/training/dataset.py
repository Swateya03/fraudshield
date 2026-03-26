"""
ml_pipeline/training/dataset.py
─────────────────────────────────
Pulls labeled training data and builds feature matrix.
Point-in-time correct features (no temporal leakage).
"""

from datetime import datetime, timedelta
from sqlalchemy import text
from typing import Tuple
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


def build_training_dataset(from_date: datetime = None,
                            to_date:   datetime = None) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Builds feature matrix X and label vector y for model training.

    Point-in-time correct:
      - Velocity counts only use transactions up to that moment
      - User average only uses prior transactions (avoids temporal leakage)
    """
    engine = get_engine()
    if to_date   is None: to_date   = datetime.utcnow()
    if from_date is None: from_date = to_date - timedelta(days=90)

    base_query = text("""
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
            fl.is_fraud
        FROM transactions t
        JOIN fraud_labels fl ON fl.transaction_id = t.id
        LEFT JOIN devices d  ON d.id = t.device_id
        WHERE t.created_at BETWEEN :from_date AND :to_date
        ORDER BY t.created_at
    """)

    with engine.connect() as conn:
        df = pd.read_sql(base_query, conn, params={
            "from_date": from_date.isoformat(),
            "to_date":   to_date.isoformat(),
        })

    if df.empty:
        raise ValueError("No labeled training data found. Run seed_data.py first.")

    # ── Velocity features (computed in pandas, not SQL) ─────
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

    # ── User average amount (point-in-time: only prior txns) ─
    df = df.sort_values("_ts")
    df["user_avg_amount_historical"] = (
        df.groupby("user_id")["amount"]
          .transform(lambda s: s.expanding().mean().shift(1))
    )

    # ── Device features ────────────────────────────────────────
    device_first = pd.to_datetime(df["device_first_seen"])
    df["device_age_days"] = (df["_ts"] - device_first).dt.days

    # ── Derived features ──────────────────────────────────────
    df["amount_ratio"]      = df["amount"] / df["user_avg_amount_historical"].fillna(500).clip(lower=1)
    df["is_new_device"]     = ((df["device_age_days"].isna()) | (df["device_age_days"] < 1)).astype(int)
    df["device_trust_score"] = (df["device_age_days"].fillna(0) / 30).clip(upper=1.0)
    df["is_late_night"]     = (df["_ts"].dt.hour < 5).astype(int)
    df["hour_of_day"]       = df["_ts"].dt.hour
    df["day_of_week"]       = df["_ts"].dt.dayofweek
    df["ip_fraud_history"]  = df["ip_address"].isin([
        "185.220.101.5", "185.220.101.6", "192.42.116.16"
    ]).astype(float)
    df["merchant_risk_score"] = df["merchant_id"].apply(
        lambda x: 1.0 if x in ("m_crypto", "m_giftcard", "m_jewelry", "m_luxury") else 0.1
    )
    df["is_new_user"]       = 0  # simplified for MVP

    # ── Feature selection ─────────────────────────────────────
    feature_cols = FeatureVector.FEATURE_NAMES
    X = df[feature_cols].fillna(0).astype(float)
    y = df["is_fraud"].astype(int)

    print(f"  Dataset: {len(df):,} rows | "
          f"Fraud: {y.sum():,} ({y.mean()*100:.1f}%) | "
          f"Features: {len(feature_cols)}")

    return X, y
