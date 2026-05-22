"""
scripts/train_model.py
───────────────────────
Train XGBoost model and register it.

Usage:
  python scripts/train_model.py                         # standard training
  USE_PU_LEARNING=true python scripts/train_model.py    # PU Learning mode
"""

from rich.console import Console
from rich.table import Table

from fraudshield_core.config import config
from ml_pipeline.training.registry import LocalFileRegistry

console = Console()


def _train_standard():
    """Standard supervised training on labeled data only."""
    from ml_pipeline.training.dataset import build_training_dataset
    from ml_pipeline.training.train import train

    console.print("[bold]Step 1/3  Loading labeled training data...[/bold]")
    X, y, groups = build_training_dataset()

    console.print("\n[bold]Step 2/3  Training XGBoost classifier...[/bold]")
    model, metadata = train(X, y, groups=groups)

    return model, metadata


def _train_pu():
    """PU Learning: uses all transactions (labeled + unlabeled)."""
    from ml_pipeline.training.dataset import build_full_dataset
    from ml_pipeline.training.pu_learning import pu_train

    console.print("[bold]Step 1/3  Loading full dataset for PU Learning...[/bold]")
    X, y_partial, groups, is_labeled = build_full_dataset()

    console.print("\n[bold]Step 2/3  Training PU Learning model...[/bold]")
    model, metadata = pu_train(X, y_partial, groups, is_labeled)

    return model, metadata


def main():
    console.print("\n[bold cyan]FraudShield — Model Training[/bold cyan]\n")

    use_pu = config.USE_PU_LEARNING
    if use_pu:
        console.print("[yellow]PU Learning mode enabled[/yellow]\n")
        model, metadata = _train_pu()
    else:
        model, metadata = _train_standard()

    # 3. Register
    console.print("\n[bold]Step 3/3  Registering model...[/bold]")
    registry = LocalFileRegistry()
    registry.save(model, metadata)
    registry.promote(metadata.version)

    table = Table(title=f"\nModel {metadata.version} — Results")
    table.add_column("Metric",    style="cyan")
    table.add_column("Value",     style="bold")
    table.add_row("AUC-ROC",      f"{metadata.auc_roc:.4f}")
    table.add_row("KS Statistic", f"{metadata.ks_statistic:.4f}")
    table.add_row("Calibration",  metadata.calibration)
    table.add_row("Precision",    f"{metadata.precision:.4f}")
    table.add_row("Recall",       f"{metadata.recall:.4f}")
    table.add_row("F1 Score",     f"{metadata.f1_score:.4f}")
    table.add_row("Threshold",    f"{metadata.threshold:.2f}")
    table.add_row("Train rows",   f"{metadata.training_rows:,}")
    console.print(table)

    console.print(f"\n[bold green]Model {metadata.version} registered and deployed![/bold green]")
    console.print(f"\n  Next step: [bold]python fraud_api/main.py[/bold]\n")


if __name__ == "__main__":
    main()
