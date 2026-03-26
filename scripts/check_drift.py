"""
scripts/check_drift.py
───────────────────────
Runs drift detection report and prints results.

Usage: python scripts/check_drift.py
"""

from rich.console import Console
from rich.table import Table
from rich import box

from ml_pipeline.monitoring.drift import run_drift_report
from fraudshield_core.config import config

console = Console()


def main():
    console.print("\n[bold cyan]FraudShield — Drift Detection Report[/bold cyan]\n")
    console.print("[dim]Comparing last 7 days vs 60-day baseline...[/dim]\n")

    report = run_drift_report(baseline_days=60, current_days=7)

    if "error" in report:
        console.print(f"[red]Error: {report['error']}[/red]")
        return

    # Summary
    max_psi     = report["max_psi"]
    rec         = report["recommendation"]
    rec_color   = {"RETRAIN_REQUIRED":"red","MONITOR":"yellow","STABLE":"green"}[rec]

    console.print(f"Baseline window: [dim]{report['baseline_window']}[/dim]  "
                  f"({report['baseline_rows']:,} transactions)")
    console.print(f"Current window:  [dim]{report['current_window']}[/dim]  "
                  f"({report['current_rows']:,} transactions)\n")

    # Feature PSI table
    table = Table(title="PSI by Feature", box=box.ROUNDED)
    table.add_column("Feature",       style="cyan", width=25)
    table.add_column("PSI Score",     style="bold", width=12)
    table.add_column("Status",        width=20)
    table.add_column("Threshold",     style="dim", width=12)

    for feature, psi in report["psi_by_feature"].items():
        if psi is None:
            status = "[dim]N/A[/dim]"
            psi_str = "N/A"
        elif psi > config.PSI_THRESHOLD:
            status = "[red]⚠ DRIFT DETECTED[/red]"
            psi_str = f"[red]{psi:.4f}[/red]"
        elif psi > 0.10:
            status = "[yellow]⚠ WARNING[/yellow]"
            psi_str = f"[yellow]{psi:.4f}[/yellow]"
        else:
            status = "[green]✓ stable[/green]"
            psi_str = f"[green]{psi:.4f}[/green]"
        table.add_row(feature, psi_str, status, str(config.PSI_THRESHOLD))

    console.print(table)

    # Overall recommendation
    console.print(f"\nOverall PSI:    [{rec_color}]{max_psi:.4f}[/{rec_color}]  "
                  f"(threshold: {config.PSI_THRESHOLD})")
    console.print(f"Recommendation: [bold {rec_color}]{rec}[/bold {rec_color}]\n")

    if rec == "RETRAIN_REQUIRED":
        console.print("[bold red]Action needed:[/bold red] Run [bold]python scripts/train_model.py[/bold]\n")
    elif rec == "MONITOR":
        console.print("[yellow]Monitor closely. Retrain if PSI continues to rise.[/yellow]\n")
    else:
        console.print("[green]Model is stable. No action needed.[/green]\n")


if __name__ == "__main__":
    main()
