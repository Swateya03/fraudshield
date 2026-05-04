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


def record_score(decision: str, strategy: str, score: float, latency_ms: int) -> None:
    """Single call to update all metrics for one scored transaction."""
    DECISIONS.labels(decision=decision, strategy=strategy).inc()
    SCORE_HISTOGRAM.observe(score)
    SCORING_LATENCY.observe(latency_ms / 1000.0)
    if latency_ms > 200:
        SLA_BREACHES.inc()


def set_model_version(version: str, strategy: str) -> None:
    """Call once at startup and after each champion promotion."""
    MODEL_INFO.labels(version=version, strategy=strategy).set(1)
