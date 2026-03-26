"""
scripts/seed_data.py
──────────────────────
Populates the database with synthetic CNP fraud data.
Run this first before anything else.

Usage: python scripts/seed_data.py
"""

import uuid
from datetime import datetime
from rich.console import Console
from rich.progress import track

from fraudshield_core.db import create_all_tables, get_engine
from fraudshield_core.redis_client import get_redis
from ml_pipeline.data.simulator import (
    generate_users, generate_devices, generate_transactions, MERCHANTS
)
from fraudshield_core.db import (
    users_table, merchants_table, devices_table,
    transactions_table, fraud_labels_table, user_risk_history_table
)
from sqlalchemy import insert

console = Console()


def seed():
    console.print("\n[bold cyan]FraudShield — Seed Data[/bold cyan]\n")

    # 1. Create tables
    console.print("[dim]Creating tables...[/dim]")
    create_all_tables()

    engine = get_engine()

    # 2. Seed merchants
    console.print("[dim]Seeding merchants...[/dim]")
    with engine.begin() as conn:
        for m in MERCHANTS:
            conn.execute(insert(merchants_table).prefix_with("OR IGNORE").values(
                id           = m["id"],
                name         = m["name"],
                category     = m["category"],
                mcc          = m["mcc"],
                city         = "Mumbai",
                state        = "MH",
                risk_level   = m["risk"],
                registered_at= datetime.utcnow(),
            ))

    # 3. Generate users
    console.print("[dim]Generating users...[/dim]")
    users = generate_users(100)
    with engine.begin() as conn:
        for u in track(users, description="Inserting users"):
            conn.execute(insert(users_table).prefix_with("OR IGNORE").values(
                id         = u["id"],
                email      = u["email"],
                phone      = u["phone"],
                risk_tier  = u["risk_tier"],
                kyc_status = u["kyc_status"],
                created_at = datetime.fromisoformat(u["created_at"]),
                updated_at = datetime.fromisoformat(u["updated_at"]),
            ))
            # Seed user_risk_history (SCD Type 2 initial record)
            conn.execute(insert(user_risk_history_table).prefix_with("OR IGNORE").values(
                id            = str(uuid.uuid4()),
                user_id       = u["id"],
                risk_tier     = u["risk_tier"],
                valid_from    = datetime.fromisoformat(u["created_at"]),
                valid_to      = None,
                changed_by    = "system",
                change_reason = "initial_registration",
            ))

    # 4. Generate devices
    console.print("[dim]Generating devices...[/dim]")
    devices = generate_devices(users)
    with engine.begin() as conn:
        for d in track(devices, description="Inserting devices"):
            conn.execute(insert(devices_table).prefix_with("OR IGNORE").values(
                id            = d["id"],
                user_id       = d["user_id"],
                device_type   = d["device_type"],
                os            = d["os"],
                browser       = d["browser"],
                first_seen_at = datetime.fromisoformat(d["first_seen_at"]),
                last_seen_at  = datetime.fromisoformat(d["last_seen_at"]),
                is_trusted    = d["is_trusted"],
            ))

    # 5. Generate transactions + labels
    console.print("[dim]Generating 90 days of transactions...[/dim]")
    transactions, labels = generate_transactions(users, devices, n_days=90)
    with engine.begin() as conn:
        for txn in track(transactions, description="Inserting transactions"):
            conn.execute(insert(transactions_table).prefix_with("OR IGNORE").values(
                id          = txn["id"],
                user_id     = txn["user_id"],
                merchant_id = txn["merchant_id"],
                device_id   = txn["device_id"],
                amount      = txn["amount"],
                currency    = txn["currency"],
                channel     = txn["channel"],
                ip_address  = txn["ip_address"],
                created_at  = datetime.fromisoformat(txn["created_at"]),
            ))

        for lbl in track(labels, description="Inserting labels"):
            conn.execute(insert(fraud_labels_table).prefix_with("OR IGNORE").values(
                id             = lbl["id"],
                transaction_id = lbl["transaction_id"],
                is_fraud       = lbl["is_fraud"],
                label_source   = lbl["label_source"],
                labeled_at     = datetime.fromisoformat(lbl["labeled_at"]),
                labeled_by     = lbl["labeled_by"],
                notes          = lbl["notes"],
            ))

    fraud_count = sum(1 for t in transactions if t["is_fraud"])
    console.print(f"\n[bold green]✓ Seed complete![/bold green]")
    console.print(f"  Merchants:    {len(MERCHANTS)}")
    console.print(f"  Users:        {len(users)}")
    console.print(f"  Devices:      {len(devices)}")
    console.print(f"  Transactions: {len(transactions):,}")
    console.print(f"  Labels:       {len(labels):,}")
    console.print(f"  Fraud rate:   {fraud_count/len(transactions)*100:.1f}%")
    console.print(f"\n  Next step: [bold]python scripts/train_model.py[/bold]\n")


if __name__ == "__main__":
    seed()
