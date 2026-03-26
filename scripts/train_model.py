"""
scripts/train_model.py
───────────────────────
Train XGBoost model and register it.

Usage: python scripts/train_model.py
"""

from rich.console import Console
from rich.table import Table

from ml_pipeline.training.dataset import build_training_dataset
from ml_pipeline.training.train import train
from ml_pipeline.training.registry import LocalFileRegistry

console = Console()


def main():
    console.print("\n[bold cyan]FraudShield — Model Training[/bold cyan]\n")

    # 1. Build dataset
    console.print("[bold]Step 1/3  Loading labeled training data...[/bold]")
    X, y = build_training_dataset()

    # 2. Train
    console.print("\n[bold]Step 2/3  Training XGBoost classifier...[/bold]")
    model, metadata = train(X, y)

    # 3. Register
    console.print("\n[bold]Step 3/3  Registering model...[/bold]")
    registry = LocalFileRegistry()
    registry.save(model, metadata)
    registry.promote(metadata.version)

    # Print results table
    table = Table(title=f"\nModel {metadata.version} — Results")
    table.add_column("Metric",    style="cyan")
    table.add_column("Value",     style="bold")
    table.add_row("AUC-ROC",    f"{metadata.auc_roc:.4f}")
    table.add_row("Precision",  f"{metadata.precision:.4f}")
    table.add_row("Recall",     f"{metadata.recall:.4f}")
    table.add_row("F1 Score",   f"{metadata.f1_score:.4f}")
    table.add_row("Threshold",  f"{metadata.threshold:.2f}")
    table.add_row("Train rows", f"{metadata.training_rows:,}")
    console.print(table)

    console.print(f"\n[bold green]✓ Model {metadata.version} registered and deployed![/bold green]")
    console.print(f"\n  Next step: [bold]python fraud_api/main.py[/bold]\n")


if __name__ == "__main__":
    main()
