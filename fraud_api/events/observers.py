"""
fraud_api/events/observers.py
──────────────────────────────
All observer handlers.
Each is a simple function that takes a FraudEvent.
Subscribe them to the publisher in main.py wire-up.
"""

from fraudshield_core.models import FraudEvent, Decision
from datetime import datetime
import uuid


def audit_log_observer(event: FraudEvent) -> None:
    """
    Writes every decision to audit log.
    In MVP: prints to console + in-memory.
    In production: writes to PostgreSQL audit_log table.
    """
    print(
        f"  [AUDIT] {event.scored_at.strftime('%H:%M:%S')} | "
        f"{event.transaction_id} | "
        f"₹{event.amount:,.0f} | "
        f"score={event.score:.2f} | "
        f"{event.decision.value.upper()}"
    )


def risk_tier_updater_observer(event: FraudEvent, user_repo=None) -> None:
    """
    Escalates user risk tier when repeated high-score events detected.
    3+ blocked transactions → risk_tier = high
    """
    if event.decision != Decision.BLOCK:
        return
    if event.score < 0.85:
        return
    # In production: use Redis INCR counter + check threshold
    # For MVP: just log the escalation trigger
    print(f"  [RISK_UPDATER] User {event.user_id} — high score block recorded")


def alert_observer(event: FraudEvent) -> None:
    """
    Sends alert for high-confidence fraud blocks.
    In MVP: prints to console.
    In production: sends SMS/email via SNS or Twilio.
    """
    if event.score >= 0.90 and event.decision == Decision.BLOCK:
        print(
            f"  [ALERT] 🚨 HIGH CONFIDENCE FRAUD | "
            f"User: {event.user_id} | "
            f"Amount: ₹{event.amount:,.0f} | "
            f"Score: {event.score:.2f}"
        )
