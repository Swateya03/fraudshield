"""
scripts/seed_data.py
──────────────────────
Populates the database with synthetic CNP fraud data.
Run this first before anything else.

Auto-wipes old data (DB, model registry, metadata) on every run to
prevent stale data confusion.

Usage:
  python scripts/seed_data.py
"""

import json
import shutil
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.progress import track

from fraudshield_core.config import config

console = Console()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _wipe_old_data() -> None:
    """Delete old database, model registry, and metadata before seeding."""
    db_url = config.DB_URL
    db_deleted = False

    if db_url.startswith("sqlite:///"):
        raw = db_url[len("sqlite:///"):]
        db_file = Path(raw).resolve()
        if not db_file.is_absolute():
            db_file = _PROJECT_ROOT / raw

        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_file) + suffix)
            if p.exists():
                try:
                    p.unlink()
                    console.print(f"[dim]Deleted {p.name}[/dim]")
                    if suffix == "":
                        db_deleted = True
                except PermissionError:
                    if suffix == "":
                        console.print("[yellow]DB file locked — truncating tables instead[/yellow]")

    if not db_deleted:
        _truncate_all_tables()

    registry = Path(config.MODEL_REGISTRY_PATH)
    if registry.exists():
        shutil.rmtree(registry)
        console.print(f"[dim]Deleted old model registry: {registry}[/dim]")

    meta_file = _PROJECT_ROOT / "local_store" / "dataset_metadata.json"
    if meta_file.exists():
        meta_file.unlink()
        console.print("[dim]Deleted old dataset_metadata.json[/dim]")


def _truncate_all_tables() -> None:
    """Fallback: clear all rows when the DB file can't be deleted."""
    from fraudshield_core.db import get_engine, metadata as db_metadata
    engine = get_engine()
    db_metadata.create_all(engine)
    with engine.begin() as conn:
        for table in reversed(db_metadata.sorted_tables):
            conn.execute(table.delete())
    console.print("[dim]Truncated all tables[/dim]")


def seed():
    console.print("\n[bold cyan]FraudShield — Seed Data[/bold cyan]\n")

    _wipe_old_data()

    from fraudshield_core.db import create_all_tables, get_engine
    from fraudshield_core.db import (
        users_table, merchants_table, devices_table,
        transactions_table, fraud_labels_table, user_risk_history_table,
    )
    from sqlalchemy import insert
    from ml_pipeline.data.simulator import (
        generate_users, generate_devices, generate_transactions,
        generate_labels, MERCHANTS, validate,
    )

    import random
    rng = random.Random(99)

    # 1. Create tables
    console.print("[dim]Creating tables...[/dim]")
    create_all_tables()
    engine = get_engine()

    # 2. Seed merchants (with diverse cities from MERCHANTS constant)
    console.print("[dim]Seeding merchants...[/dim]")
    with engine.begin() as conn:
        for m in MERCHANTS:
            registered = datetime.utcnow() - timedelta(days=rng.randint(180, 730))
            conn.execute(insert(merchants_table).prefix_with("OR IGNORE").values(
                id            = m["id"],
                name          = m["name"],
                category      = m["category"],
                mcc           = m["mcc"],
                city          = m.get("city", "Mumbai"),
                state         = m.get("state", "MH"),
                risk_level    = m["risk"],
                registered_at = registered,
            ))

    # 3. Generate users
    console.print("[dim]Generating users...[/dim]")
    users = generate_users(2000)
    with engine.begin() as conn:
        for u in track(users, description="Inserting users"):
            conn.execute(insert(users_table).prefix_with("OR IGNORE").values(
                id         = u["id"],
                email      = u["email"],
                phone      = u["phone"],
                risk_tier  = u["risk_tier"],
                kyc_status = u["kyc_status"],
                city       = u.get("city"),
                state      = u.get("state"),
                created_at = datetime.fromisoformat(u["created_at"]),
                updated_at = datetime.fromisoformat(u["updated_at"]),
            ))
            conn.execute(insert(user_risk_history_table).prefix_with("OR IGNORE").values(
                id            = str(uuid.uuid4()),
                user_id       = u["id"],
                risk_tier     = u["risk_tier"],
                valid_from    = datetime.fromisoformat(u["created_at"]),
                valid_to      = None,
                changed_by    = "system",
                change_reason = "initial_registration",
            ))

            # SCD2 tier escalation for ~12% of users
            if rng.random() < 0.12:
                created = datetime.fromisoformat(u["created_at"])
                escalation_date = created + timedelta(days=rng.randint(14, 90))
                new_tier = rng.choice(["medium", "high"])
                conn.execute(insert(user_risk_history_table).prefix_with("OR IGNORE").values(
                    id            = str(uuid.uuid4()),
                    user_id       = u["id"],
                    risk_tier     = new_tier,
                    valid_from    = escalation_date,
                    valid_to      = None,
                    changed_by    = "risk_engine",
                    change_reason = "automated_escalation",
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

    # 5. Generate transactions (labels are separate now)
    console.print("[dim]Generating 90 days of transactions...[/dim]")
    transactions = generate_transactions(users, devices, n_days=90)
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
                txn_source  = "seed",
            ))

    # 6. Generate labels (sparse, with noise)
    console.print("[dim]Generating sparse labels...[/dim]")
    user_avg_amounts = {u["id"]: u.get("_avg_amount", 500.0) for u in users}
    labels = generate_labels(transactions, user_avg_amounts)
    with engine.begin() as conn:
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

    # 7. Validate
    r = validate(transactions, labels)
    assert r["ALL_PASS"], f"Data quality validation failed: {r}"

    # 8. Dataset metadata
    meta_dir = _PROJECT_ROOT / "local_store"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at":   datetime.utcnow().isoformat(),
        "n_users":        len(users),
        "n_merchants":    len(MERCHANTS),
        "n_devices":      len(devices),
        "n_transactions": len(transactions),
        "n_labels":       len(labels),
        "fraud_rate_pct": round(sum(1 for t in transactions if t.get("is_fraud")) / max(len(transactions), 1) * 100, 2),
        "label_coverage_pct": r["label_coverage_pct"],
        "fraud_rate_in_labels_pct": r["fraud_rate_in_labels_pct"],
        "validation":     r,
    }
    (meta_dir / "dataset_metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    fraud_count = sum(1 for t in transactions if t.get("is_fraud"))
    console.print(f"\n[bold green]✓ Seed complete![/bold green]")
    console.print(f"  Merchants:    {len(MERCHANTS)}")
    console.print(f"  Users:        {len(users)}")
    console.print(f"  Devices:      {len(devices)}")
    console.print(f"  Transactions: {len(transactions):,}")
    console.print(f"  Labels:       {len(labels):,}")
    console.print(f"  Fraud rate:   {fraud_count/len(transactions)*100:.1f}%")
    console.print(f"  Label cov:    {r['label_coverage_pct']}%")
    console.print(f"  Validation:   {'ALL PASS' if r['ALL_PASS'] else 'SOME FAILED'}")
    console.print(f"\n  Next step: [bold]python scripts/train_model.py[/bold]\n")


if __name__ == "__main__":
    seed()
