"""
scripts/retrain_loop.py
────────────────────────
Drift-triggered champion/challenger retraining loop.

Steps:
  1. Check feature drift (PSI across all monitored features)
  2. If RETRAIN_REQUIRED (or --force): train a challenger model
  3. Compare challenger AUC vs current champion AUC
  4. Promote challenger if it wins or is within acceptable tolerance
  5. Write JSON result file for CI job summary

Exit codes:
  0 — completed (whether or not retraining happened)
  1 — fatal error during setup or training

Usage:
  python scripts/retrain_loop.py
  python scripts/retrain_loop.py --force true
  python scripts/retrain_loop.py --force true --output result.json
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional, Tuple

from rich.console import Console

from fraudshield_core.config import config
from ml_pipeline.monitoring.drift import run_drift_report
from ml_pipeline.training.registry import LocalFileRegistry
from fraudshield_core.models import ModelMetadata

console = Console()

# Challenger must not be more than this much worse than champion to be promoted.
# A negative delta means challenger can be slightly below champion and still win
# (guards against noise in small test sets flipping a good model).
MIN_AUC_IMPROVEMENT = -0.005


def _load_champion(registry: LocalFileRegistry) -> Tuple[Optional[str], float]:
    """Returns (version, auc_roc) for the current champion, or (None, 0.0)."""
    try:
        _, meta = registry.load("current")
        return meta.version, meta.auc_roc
    except FileNotFoundError:
        return None, 0.0


def _train_challenger() -> Tuple[object, ModelMetadata]:
    from ml_pipeline.training.dataset import build_training_dataset
    from ml_pipeline.training.train import train

    X, y, groups = build_training_dataset()
    return train(X, y, groups=groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Drift-triggered retrain loop")
    parser.add_argument(
        "--force",
        type=lambda v: v.lower() == "true",
        default=False,
        help="Retrain even if no drift is detected (for manual / CI override)",
    )
    parser.add_argument(
        "--output",
        default="retrain_result.json",
        help="Path to write the JSON result file",
    )
    args = parser.parse_args()

    result = {
        "run_at": datetime.utcnow().isoformat(),
        "recommendation": "STABLE",
        "max_psi": 0.0,
        "retrain_triggered": False,
        "champion_version": None,
        "champion_auc": None,
        "challenger_version": None,
        "challenger_auc": None,
        "promoted": False,
        "promotion_reason": None,
    }

    # ── Step 1: Drift check ───────────────────────────────────────────────────
    console.print("\n[bold cyan]FraudShield — Retrain Loop[/bold cyan]\n")
    console.print("[bold]Step 1/3  Running drift check...[/bold]")

    report = run_drift_report(baseline_days=60, current_days=7)

    if "error" in report:
        console.print(f"[yellow]  Drift check inconclusive: {report['error']}[/yellow]")
        result["recommendation"] = "SKIPPED"
    else:
        result["max_psi"] = report["max_psi"]
        result["recommendation"] = report["recommendation"]
        console.print(
            f"  Max PSI: [bold]{report['max_psi']:.4f}[/bold]  →  "
            f"[{'red' if report['recommendation'] == 'RETRAIN_REQUIRED' else 'yellow' if report['recommendation'] == 'MONITOR' else 'green'}]"
            f"{report['recommendation']}[/]"
        )

    should_retrain = args.force or result["recommendation"] == "RETRAIN_REQUIRED"

    if not should_retrain:
        console.print(
            f"\n[green]No retraining needed. "
            f"{'Drift within safe range.' if result['recommendation'] == 'MONITOR' else 'Model stable.'}[/green]\n"
        )
        _write_result(result, args.output)
        return

    if args.force and result["recommendation"] != "RETRAIN_REQUIRED":
        console.print("[yellow]  --force flag set: retraining regardless of drift status[/yellow]")

    # ── Step 2: Load champion baseline ───────────────────────────────────────
    console.print(f"\n[bold]Step 2/3  Loading champion...[/bold]")
    registry = LocalFileRegistry()
    champion_version, champion_auc = _load_champion(registry)

    result["champion_version"] = champion_version
    result["champion_auc"] = champion_auc
    result["retrain_triggered"] = True

    if champion_version:
        console.print(f"  Champion: [cyan]{champion_version}[/cyan]  AUC {champion_auc:.4f}")
    else:
        console.print("  [yellow]No champion found — first trained model will be auto-promoted[/yellow]")

    # ── Step 3: Train challenger ──────────────────────────────────────────────
    console.print(f"\n[bold]Step 3/3  Training challenger...[/bold]")

    try:
        model, metadata = _train_challenger()
    except Exception as exc:
        console.print(f"[red]  Training failed: {exc}[/red]")
        result["promotion_reason"] = f"training_error: {exc}"
        _write_result(result, args.output)
        sys.exit(1)

    result["challenger_version"] = metadata.version
    result["challenger_auc"] = metadata.auc_roc
    console.print(f"  Challenger: [cyan]{metadata.version}[/cyan]  AUC {metadata.auc_roc:.4f}")

    # ── Promotion decision ────────────────────────────────────────────────────
    registry.save(model, metadata)

    if champion_version is None:
        # No champion exists yet — always promote the first trained model
        registry.promote(metadata.version)
        result["promoted"] = True
        result["promotion_reason"] = "first_model"
        console.print(f"\n[bold green]✓ {metadata.version} promoted (first model in registry)[/bold green]")

    else:
        delta = metadata.auc_roc - champion_auc
        if delta >= MIN_AUC_IMPROVEMENT:
            registry.promote(metadata.version)
            result["promoted"] = True
            result["promotion_reason"] = f"auc_delta={delta:+.4f}"
            console.print(
                f"\n[bold green]✓ {metadata.version} promoted "
                f"(Δ AUC {delta:+.4f} vs champion {champion_version})[/bold green]"
            )
        else:
            result["promotion_reason"] = f"challenger_lost auc_delta={delta:+.4f}"
            console.print(
                f"\n[yellow]Challenger did not improve "
                f"(Δ AUC {delta:+.4f}). Champion [cyan]{champion_version}[/cyan] retained.[/yellow]"
            )

    _write_result(result, args.output)


def _write_result(result: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    console.print(f"\n  Result written → [dim]{path}[/dim]\n")


if __name__ == "__main__":
    main()
