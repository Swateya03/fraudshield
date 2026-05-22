"""
scripts/inject_drift.py
────────────────────────
Simulates Month 2 fraud pattern change.

Old pattern (Month 1 — what model was trained on):
  new device + Tor IP + late night + crypto merchant + 10-40x amount

New pattern (Month 2 — what attackers switched to):
  KNOWN device (compromised) + clean VPN IP + DAYTIME + same bad merchant + split amounts

The model was never trained on this pattern.
PSI will spike on: device_trust_score, ip_fraud_history, is_late_night
"""

import random
import uuid
from datetime import datetime, timedelta
from sqlalchemy import insert, text
from rich.console import Console

from fraudshield_core.db import get_engine, transactions_table, fraud_labels_table

console = Console()

# Month 2 fraud pattern — what the model has NOT seen
def make_drift_fraud_txn(user_id: str, avg_amount: float, base_time: datetime) -> dict:
    """
    New fraud pattern:
    - Uses a KNOWN device ID format (looks trusted)
    - Uses clean VPN IP (not in FRAUD_IPS list)
    - Operates DAYTIME (9am-5pm) not late night
    - Splits amounts into smaller transactions (avoids amount threshold)
    - Still targets bad merchants (hard to change this)
    """
    rng = random.Random()

    # Known-looking device (compromised legitimate device)
    device_id = f"d_{uuid.uuid4().hex[:12]}"        # looks like a trusted device

    # Clean VPN IP — not in FRAUD_IPS, looks legitimate
    clean_vpn_ips = [
        "45.142.212.100", "45.142.212.101",
        "104.21.45.67",   "104.21.45.68",
        "172.67.182.33",  "172.67.182.34",
    ]
    ip = rng.choice(clean_vpn_ips)

    # Daytime operation — attacker in different timezone
    hour = rng.choice([9, 10, 11, 14, 15, 16])

    # Smaller split amounts — avoid triggering amount thresholds
    # Instead of 1x ₹40,000 → 3x ₹12,000
    amount = avg_amount * rng.uniform(1.5, 3.5)     # much smaller than 8-40x

    # Still targets high-risk merchants
    from fraudshield_core.config import config as _cfg
    merchant_id = rng.choice(sorted(_cfg.HIGH_RISK_MERCHANT_IDS))

    ts = base_time.replace(
        hour=hour, minute=rng.randint(0, 59),
        second=rng.randint(0, 59), microsecond=0
    )

    return {
        "id":          f"txn_{uuid.uuid4().hex[:12]}",
        "user_id":     user_id,
        "merchant_id": merchant_id,
        "device_id":   device_id,
        "amount":      round(amount, 2),
        "currency":    "INR",
        "channel":     "online",
        "ip_address":  ip,
        "created_at":  ts.isoformat(),
        "is_fraud":    True,
    }


def inject(n_days: int = 7, txns_per_day: int = 400):
    console.print("\n[bold cyan]FraudShield — Concept Drift Injection[/bold cyan]\n")
    console.print("[bold]Injecting Month 2 fraud pattern (last 7 days)...[/bold]")
    console.print("[dim]New pattern: known device + clean IP + daytime + split amounts[/dim]\n")

    engine = get_engine()
    now    = datetime.utcnow()

    # Get real user IDs + their avg amounts from DB
    # Prefer labeled non-fraud users; fall back to any transaction users.
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT t.user_id, AVG(t.amount) as avg_amount
            FROM transactions t
            LEFT JOIN fraud_labels fl ON fl.transaction_id = t.id
            WHERE fl.is_fraud = 0 OR fl.id IS NULL
            GROUP BY t.user_id
            LIMIT 30
        """)).fetchall()

    users = [(r.user_id, r.avg_amount) for r in rows]
    if not users:
        console.print("[red]No users found. Run seed_data.py first.[/red]")
        return

    injected = 0
    for day_offset in range(n_days, 0, -1):
        day = now - timedelta(days=day_offset)
        for _ in range(txns_per_day):
            user_id, avg_amount = random.choice(users)
            txn = make_drift_fraud_txn(user_id, avg_amount, day)

            with engine.begin() as conn:
                # Insert transaction
                conn.execute(
                    insert(transactions_table).prefix_with("OR IGNORE").values(
                        id          = txn["id"],
                        user_id     = txn["user_id"],
                        merchant_id = txn["merchant_id"],
                        device_id   = txn["device_id"],
                        amount      = txn["amount"],
                        currency    = txn["currency"],
                        channel     = txn["channel"],
                        ip_address  = txn["ip_address"],
                        created_at  = datetime.fromisoformat(txn["created_at"]),
                    )
                )
                # Insert label — chargeback arrives 5-15 days later
                label_delay = random.randint(5, 15)
                conn.execute(
                    insert(fraud_labels_table).prefix_with("OR IGNORE").values(
                        id             = str(uuid.uuid4()),
                        transaction_id = txn["id"],
                        is_fraud       = True,
                        label_source   = "chargeback",
                        labeled_at     = (
                            datetime.fromisoformat(txn["created_at"]) +
                            timedelta(days=label_delay)
                        ),
                        labeled_by     = None,
                        notes          = "drift_injection_month2",
                    )
                )
            injected += 1

    console.print(f"[bold green][ok] Injected {injected} drift-phase transactions[/bold green]")
    console.print(f"\n  Pattern change summary:")
    console.print(f"  [red]Old:[/red] new device + Tor IP + 3am + 10-40x amount")
    console.print(f"  [yellow]New:[/yellow] known device + clean IP + 9am + 1.5-3.5x amount")
    console.print(f"\n  Features that will drift:")
    console.print(f"  [yellow]device_trust_score[/yellow]  -> was 0.0 (new device), now ~0.5 (known)")
    console.print(f"  [yellow]ip_fraud_history[/yellow]    -> was 1.0 (Tor), now 0.0 (clean VPN)")
    console.print(f"  [yellow]is_late_night[/yellow]       -> was 1.0 (3am), now 0.0 (9am)")
    console.print(f"  [yellow]amount_ratio[/yellow]        -> was 10-40x, now 1.5-3.5x")
    console.print(f"\n  Next: [bold]python scripts/check_drift.py[/bold]\n")


if __name__ == "__main__":
    inject()