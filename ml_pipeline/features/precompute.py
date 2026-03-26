"""
ml_pipeline/features/precompute.py
────────────────────────────────────
Batch feature pre-computation.
Runs daily (manually in MVP, Airflow DAG in production).

Reads:  PostgreSQL transactions (last 30 days)
Writes: Redis feature store (offline features per user)

This is what makes the online scoring fast:
  Instead of running 8 SQL queries per transaction,
  we pre-compute everything nightly and serve from Redis in <1ms.
"""

from datetime import datetime, timedelta
from sqlalchemy import text
import json

from fraudshield_core.db import get_engine
from fraudshield_core.redis_client import get_redis
from ml_pipeline.features.feature_store import RedisFeatureStore


def compute_and_store_features(days_lookback: int = 30) -> dict:
    """
    Compute offline features for all active users.
    Stores results in Redis feature store.

    Returns summary stats.
    """
    engine = get_engine()
    store  = RedisFeatureStore()
    cutoff = datetime.utcnow() - timedelta(days=days_lookback)

    query = text("""
        SELECT
            t.user_id,
            COUNT(*)                          AS txn_count,
            AVG(t.amount)                     AS avg_amount,
            MAX(t.amount)                     AS max_amount,
            MIN(t.amount)                     AS min_amount,
            COUNT(*) * 1.0 / :days            AS avg_velocity_per_day,
            COUNT(DISTINCT t.merchant_id)     AS distinct_merchants,
            COUNT(DISTINCT t.ip_address)      AS distinct_ips,
            COUNT(DISTINCT t.device_id)       AS distinct_devices,
            SUM(CASE WHEN strftime('%H', t.created_at) < '05'
                     THEN 1 ELSE 0 END)       AS late_night_txns
        FROM transactions t
        WHERE t.created_at >= :cutoff
        GROUP BY t.user_id
    """)

    users_updated = 0
    with engine.connect() as conn:
        rows = conn.execute(query, {
            "cutoff": cutoff.isoformat(),
            "days":   days_lookback,
        }).fetchall()

        for row in rows:
            features = {
                "avg_amount":          float(row.avg_amount or 500),
                "max_amount":          float(row.max_amount or 0),
                "min_amount":          float(row.min_amount or 0),
                "avg_velocity_per_day": float(row.avg_velocity_per_day or 1),
                "distinct_merchants":  int(row.distinct_merchants or 0),
                "distinct_ips":        int(row.distinct_ips or 0),
                "distinct_devices":    int(row.distinct_devices or 0),
                "late_night_ratio":    (
                    row.late_night_txns / max(row.txn_count, 1)
                ),
                "computed_at":         datetime.utcnow().isoformat(),
            }
            store.set_offline_features(row.user_id, features)
            users_updated += 1

    return {
        "users_updated": users_updated,
        "lookback_days": days_lookback,
        "computed_at":   datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    result = compute_and_store_features()
    print(f"✓ Feature precompute complete: {result}")
