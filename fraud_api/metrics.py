"""
fraud_api/metrics.py
─────────────────────
Prometheus metric definitions for FraudShield.

All metrics are module-level singletons — safe to import anywhere.
Exposed at GET /metrics by the ASGI sub-app mounted in main.py.

Metrics:
  fraudshield_decisions_total        Counter  — allow/review/block per strategy
  fraudshield_score_histogram        Histogram — fraud score distribution (0-1)
  fraudshield_scoring_latency_seconds Histogram — end-to-end scoring latency
  fraudshield_sla_breaches_total     Counter  — requests that exceeded 200ms SLA
  fraudshield_model_info             Gauge    — active model version (value=1)
"""

from prometheus_client import Counter, Histogram, Gauge, REGISTRY

# ── Decision counter ──────────────────────────────────────────────────────────
DECISIONS = Counter(
    "fraudshield_decisions_total",
    "Total fraud decisions made",
    ["decision", "strategy"],   # labels: decision=allow|review|block, strategy=name
)

# ── Score distribution ────────────────────────────────────────────────────────
SCORE_HISTOGRAM = Histogram(
    "fraudshield_score",
    "Distribution of fraud probability scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# Labelled by ab_variant — allows Grafana to overlay champion vs challenger distributions
SCORE_BY_VARIANT = Histogram(
    "fraudshield_score_by_variant",
    "Fraud score distribution split by A/B variant",
    ["ab_variant"],
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ── Latency ───────────────────────────────────────────────────────────────────
SCORING_LATENCY = Histogram(
    "fraudshield_scoring_latency_seconds",
    "End-to-end scoring latency in seconds",
    buckets=[0.005, 0.010, 0.025, 0.050, 0.100, 0.150, 0.200, 0.300, 0.500, 1.0],
)

# ── SLA breaches ──────────────────────────────────────────────────────────────
SLA_BREACHES = Counter(
    "fraudshield_sla_breaches_total",
    "Requests that exceeded the 200ms SLA",
)

# ── Model info ────────────────────────────────────────────────────────────────
MODEL_INFO = Gauge(
    "fraudshield_model_info",
    "Active model version (value is always 1, use label to read version)",
    ["version", "strategy"],
)

ROLLBACK_TARGET = Gauge(
    "fraudshield_rollback_target",
    "Previous champion version available for rollback (value is always 1, use label)",
    ["version", "strategy"],
)


def record_score(decision: str, strategy: str, score: float, latency_ms: int,
                 ab_variant: str = "champion") -> None:
    """Single call to update all metrics for one scored transaction."""
    DECISIONS.labels(decision=decision, strategy=strategy).inc()
    SCORE_HISTOGRAM.observe(score)
    SCORE_BY_VARIANT.labels(ab_variant=ab_variant).observe(score)
    SCORING_LATENCY.observe(latency_ms / 1000.0)
    if latency_ms > 200:
        SLA_BREACHES.inc()


def set_model_version(version: str, strategy: str) -> None:
    """Call once at startup and after each champion promotion."""
    MODEL_INFO.labels(version=version, strategy=strategy).set(1)


def set_rollback_target(version: str, strategy: str) -> None:
    """Set the previous champion version as the rollback target."""
    ROLLBACK_TARGET.labels(version=version, strategy=strategy).set(1)


# ── PSI drift scores ───────────────────���──────────────────────��───────────────
PSI_GAUGE = Gauge(
    "fraudshield_psi_score",
    "Population Stability Index per feature (updated by drift monitor)",
    ["feature"],
)


def update_psi_metrics(psi_by_feature: dict) -> None:
    """
    Push PSI values into Prometheus after a drift check run.
    Called by the drift monitor and by POST /v1/model/drift.

    psi_by_feature: {"amount_log": 0.04, "velocity_1h": 0.23, ...}
    """
    for feature, value in psi_by_feature.items():
        if value is not None:
            PSI_GAUGE.labels(feature=feature).set(float(value))
