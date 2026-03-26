"""
ml_pipeline/monitoring/drift.py
────────────────────────────────
Population Stability Index (PSI) drift detection.

PSI < 0.10  → stable, no action
PSI 0.10-0.25 → slight drift, monitor
PSI > 0.25  → significant drift, retrain

Run: python scripts/check_drift.py
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import text
from typing import Dict

from fraudshield_core.db import get_engine
from fraudshield_core.config import config
from fraudshield_core.models import FeatureVector


def calculate_psi(expected: np.ndarray,
                  actual: np.ndarray,
                  bins: int = 10) -> float:
    """
    Population Stability Index between two distributions.
    Measures how much a feature has drifted from training baseline.
    """
    # Bin edges from expected (training) distribution
    breakpoints = np.percentile(expected, np.linspace(0, 100, bins + 1))
    breakpoints = np.unique(breakpoints)

    expected_counts = np.histogram(expected, bins=breakpoints)[0]
    actual_counts   = np.histogram(actual,   bins=breakpoints)[0]

    # Avoid division by zero
    expected_pct = (expected_counts + 0.001) / len(expected)
    actual_pct   = (actual_counts   + 0.001) / len(actual)

    psi = np.sum(
        (actual_pct - expected_pct) * np.log(actual_pct / expected_pct)
    )
    return float(psi)


def run_drift_report(baseline_days: int = 60,
                     current_days:  int = 7) -> Dict:
    """
    Compare current feature distribution vs training baseline.
    Returns PSI per feature + overall recommendation.
    """
    engine = get_engine()
    now    = datetime.utcnow()

    baseline_cutoff = now - timedelta(days=baseline_days)
    current_cutoff  = now - timedelta(days=current_days)

    # Pull raw transaction data for both windows
    query = text("""
        SELECT
            t.amount,
            t.ip_address,
            t.device_id,
            t.created_at,
            t.merchant_id,
            (SELECT COUNT(*) FROM transactions t2
             WHERE t2.user_id = t.user_id
               AND t2.created_at BETWEEN datetime(t.created_at, '-1 hour')
                                     AND t.created_at) AS velocity_1h
        FROM transactions t
        WHERE t.created_at >= :cutoff
    """)

    with engine.connect() as conn:
        baseline_df = pd.read_sql(
            query, conn,
            params={"cutoff": baseline_cutoff.isoformat()}
        )
        current_df = pd.read_sql(
            query, conn,
            params={"cutoff": current_cutoff.isoformat()}
        )

    if baseline_df.empty or current_df.empty:
        return {"error": "Not enough data for drift detection"}

    # ── Compute PSI for key features ─────────────────────────
    results = {}
    FRAUD_IPS = {"185.220.101.5", "185.220.101.6", "192.42.116.16"}
    HIGH_RISK_MERCHANTS = {"m_crypto", "m_giftcard", "m_jewelry"}

    feature_extractors = {
        "amount_log":           lambda df: np.log1p(df["amount"]),
        "velocity_1h":          lambda df: df["velocity_1h"].fillna(0),
        "is_late_night":        lambda df: df["created_at"].apply(
                                    lambda x: 1.0 if isinstance(x, str) and int(x[11:13]) < 5 else 0.0
                                ),
        "ip_fraud_history":     lambda df: df["ip_address"].isin(FRAUD_IPS).astype(float),
        "merchant_risk_score":  lambda df: df["merchant_id"].isin(HIGH_RISK_MERCHANTS).astype(float),
        "hour_of_day":          lambda df: df["created_at"].apply(
                                    lambda x: float(x[11:13]) if isinstance(x, str) else 0.0
                                ),
    }

    psi_results = {}
    max_psi     = 0.0

    for feature_name, extractor in feature_extractors.items():
        try:
            baseline_vals = extractor(baseline_df).values
            current_vals  = extractor(current_df).values
            psi           = calculate_psi(baseline_vals, current_vals)
            psi_results[feature_name] = psi
            max_psi = max(max_psi, psi)
        except Exception as e:
            psi_results[feature_name] = None

    # ── Build report ──────────────────────────────────────────
    recommendation = (
        "RETRAIN_REQUIRED" if max_psi > config.PSI_THRESHOLD else
        "MONITOR"          if max_psi > 0.10 else
        "STABLE"
    )

    return {
        "computed_at":      datetime.utcnow().isoformat(),
        "baseline_window":  f"last {baseline_days} days",
        "current_window":   f"last {current_days} days",
        "baseline_rows":    len(baseline_df),
        "current_rows":     len(current_df),
        "psi_by_feature":   psi_results,
        "max_psi":          round(max_psi, 4),
        "psi_threshold":    config.PSI_THRESHOLD,
        "recommendation":   recommendation,
    }
