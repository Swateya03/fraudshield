"""
scripts/score_transaction.py
──────────────────────────────
Interactive terminal UI for scoring transactions + labeling.
This is the feedback loop closing tool.

Controls:
  L → Mark as Legitimate (writes label: is_fraud=False)
  F → Confirm as Fraud    (writes label: is_fraud=True)
  S → Skip (no label)
  Q → Quit

Usage: python scripts/score_transaction.py
"""

import uuid
import time
import httpx
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box
from sqlalchemy import text

from fraudshield_core.db import get_engine, fraud_labels_table, fraud_scores_table
from fraudshield_core.config import config
from sqlalchemy import insert

console = Console()
API_URL = f"http://localhost:{config.API_PORT}"
HEADERS = {
    "Authorization": f"Bearer {config.API_TOKEN}",
    "Content-Type":  "application/json",
}


def fetch_review_queue(limit: int = 20) -> list:
    """Get transactions scored as 'review' with no label yet."""
    engine = get_engine()
    query  = text("""
        SELECT t.id, t.user_id, t.amount, t.channel, t.ip_address,
               t.created_at, t.merchant_id, t.device_id,
               fs.score, fs.decision, fs.reason_codes
        FROM transactions t
        JOIN fraud_scores fs ON fs.transaction_id = t.id
        LEFT JOIN fraud_labels fl ON fl.transaction_id = t.id
        WHERE fl.id IS NULL
        ORDER BY fs.score DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"limit": limit}).fetchall()
    return [dict(r._mapping) for r in rows]


def score_new_transaction(txn_data: dict) -> dict:
    """Score a transaction via the API."""
    try:
        resp = httpx.post(
            f"{API_URL}/v1/transactions/score",
            json=txn_data, headers=HEADERS, timeout=5
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def display_transaction(txn: dict, idx: int, total: int) -> None:
    """Render the transaction review card in terminal."""
    import json

    score    = float(txn.get("score", 0))
    decision = str(txn.get("decision","?")).upper()
    amount   = float(txn.get("amount", 0))

    # Score bar
    bar_len   = 30
    filled    = int(score * bar_len)
    bar       = "█" * filled + "▒" * (bar_len - filled)
    bar_color = "red" if score > 0.85 else "yellow" if score > 0.5 else "green"

    # Decision color
    dec_color = {"BLOCK":"red","REVIEW":"yellow","ALLOW":"green"}.get(decision,"white")

    console.print(f"\n[dim]Transaction {idx}/{total}[/dim]")
    console.print(Panel(
        f"[bold]Transaction:[/bold]  {txn.get('id','?')}\n"
        f"[bold]User:[/bold]         {txn.get('user_id','?')}\n"
        f"[bold]Amount:[/bold]       [cyan]₹{amount:,.2f}[/cyan]\n"
        f"[bold]Merchant:[/bold]     {txn.get('merchant_id','?')}\n"
        f"[bold]Channel:[/bold]      {txn.get('channel','?')}\n"
        f"[bold]Device:[/bold]       {'[yellow]NEW DEVICE ⚠[/yellow]' if not txn.get('device_id') else txn.get('device_id','?')[:12]}\n"
        f"[bold]IP:[/bold]           {txn.get('ip_address','N/A')}\n"
        f"[bold]Time:[/bold]         {str(txn.get('created_at','?'))[:19]}\n\n"
        f"[bold]FRAUD SCORE:[/bold]  [{bar_color}]{score:.4f}  {bar}[/{bar_color}]  "
        f"[bold {dec_color}]{decision}[/bold {dec_color}]",
        title="[bold]FraudShield — Transaction Review[/bold]",
        border_style=dec_color,
    ))

    # Reason codes
    reason_codes = txn.get("reason_codes", "[]")
    if isinstance(reason_codes, str):
        import json as _json
        reason_codes = _json.loads(reason_codes)

    if reason_codes:
        table = Table(box=box.SIMPLE, show_header=True)
        table.add_column("Reason Code",    style="cyan")
        table.add_column("Contribution",   style="bold")
        table.add_column("Impact",         style="")
        for rc in reason_codes[:5]:
            contrib = float(rc.get("contribution", 0))
            impact_bar = "█" * int(abs(contrib) * 20)
            table.add_row(rc["code"], f"{contrib:+.4f}", impact_bar)
        console.print(table)


def write_label(transaction_id: str, is_fraud: bool,
                labeled_by: str = "analyst") -> None:
    engine = get_engine()
    with engine.begin() as conn:
        # Check if label already exists
        existing = conn.execute(text(
            "SELECT id FROM fraud_labels WHERE transaction_id = :tid"
        ), {"tid": transaction_id}).fetchone()
        if existing:
            console.print(f"  [yellow]Label already exists for {transaction_id}[/yellow]")
            return
        conn.execute(insert(fraud_labels_table).values(
            id             = str(uuid.uuid4()),
            transaction_id = transaction_id,
            is_fraud       = is_fraud,
            label_source   = "manual_review",
            labeled_at     = datetime.utcnow(),
            labeled_by     = labeled_by,
            notes          = None,
        ))
    label_str = "[red]FRAUD[/red]" if is_fraud else "[green]LEGITIMATE[/green]"
    console.print(f"  ✓ Labeled as {label_str}")


def main():
    console.print("\n[bold cyan]FraudShield — Transaction Review UI[/bold cyan]")
    console.print("[dim]Fetching review queue...[/dim]\n")

    queue   = fetch_review_queue(limit=50)
    total   = len(queue)

    if total == 0:
        console.print("[yellow]No transactions in review queue.[/yellow]")
        console.print("Run [bold]python scripts/seed_data.py[/bold] first.\n")
        return

    console.print(f"[green]{total} transactions in review queue[/green]\n")
    console.print("[dim]Controls: [L] Legitimate  [F] Fraud  [S] Skip  [Q] Quit[/dim]\n")

    labeled = 0
    for idx, txn in enumerate(queue, 1):
        display_transaction(txn, idx, total)

        while True:
            choice = Prompt.ask(
                "[bold]Label[/bold]",
                choices=["l", "f", "s", "q", "L", "F", "S", "Q"],
                default="s"
            ).upper()

            if choice == "L":
                write_label(txn["id"], is_fraud=False)
                labeled += 1
                break
            elif choice == "F":
                write_label(txn["id"], is_fraud=True)
                labeled += 1
                break
            elif choice == "S":
                console.print("  [dim]Skipped[/dim]")
                break
            elif choice == "Q":
                console.print(f"\n[green]Session complete. Labeled: {labeled}[/green]")
                if labeled > 0:
                    console.print(f"  Run [bold]python scripts/train_model.py[/bold] to retrain\n")
                return

    console.print(f"\n[bold green]✓ Review session complete![/bold green]")
    console.print(f"  Labeled: {labeled} / {total}")
    if labeled > 0:
        console.print(f"\n  Run [bold]python scripts/train_model.py[/bold] to retrain on new labels\n")


if __name__ == "__main__":
    main()
