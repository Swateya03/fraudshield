"""
scripts/auto_rollback.py
─────────────────────────
Post-promotion health check with automatic rollback.

Two check modes run in sequence:

  1. Offline check (always runs)
     Validates the current champion's training metrics against minimum
     thresholds.  Catches cases where a dangerously weak model was
     promoted (e.g., AUC < 0.75, precision < 0.60).

  2. Online check (runs when Prometheus is reachable)
     Queries live production metrics over a configurable window:
       • Block-rate deviation  — if the live block-rate deviates more
         than BLOCK_RATE_MAX_DEVIATION percentage points from the
         model's training fraud_rate, the model may be miscalibrated.
       • SLA breach rate       — if > SLA_BREACH_MAX_RATE of requests
         exceed the 200 ms latency SLA, a latency regression occurred.

If either check fails and a previous champion exists in the registry,
the script promotes the previous version and records the reason.

Exit codes:
  0 — health check passed, no rollback
  2 — rollback triggered (model degradation detected)
  1 — fatal error (registry missing, etc.)

Usage:
  python scripts/auto_rollback.py
  python scripts/auto_rollback.py --dry-run
  python scripts/auto_rollback.py --prometheus-url http://localhost:9090 --window-minutes 30
  python scripts/auto_rollback.py --output rollback_result.json
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import URLError

from rich.console import Console

from ml_pipeline.training.registry import LocalFileRegistry

console = Console()

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_AUC_ROC              = 0.70   # absolute floor — anything below is dangerously weak
MIN_PRECISION            = 0.60   # false-positive floor
BLOCK_RATE_MAX_DEVIATION = 15.0   # pp deviation from model's training fraud_rate
SLA_BREACH_MAX_RATE      = 0.05   # 5 % of decisions may breach 200 ms

DEFAULT_PROMETHEUS_URL   = "http://localhost:9090"
DEFAULT_WINDOW_MINUTES   = 30
MIN_DECISIONS_FOR_ONLINE = 10     # skip online check if traffic is too low


# ── Prometheus helpers ────────────────────────────────────────────────────────

def _query_prometheus(base_url: str, query: str) -> Optional[float]:
    """Execute an instant PromQL query; return scalar float or None on any error."""
    params = urlencode({"query": query})
    url = f"{base_url}/api/v1/query?{params}"
    try:
        with urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        result = data["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])
    except (URLError, KeyError, ValueError, IndexError):
        return None


def _get_live_metrics(prometheus_url: str, window: int) -> dict:
    w = f"{window}m"
    return {
        "block_rate_pct": _query_prometheus(
            prometheus_url,
            f'100 * sum(rate(fraudshield_decisions_total{{decision="block"}}[{w}]))'
            f' / sum(rate(fraudshield_decisions_total[{w}]))',
        ),
        "sla_breach_rate": _query_prometheus(
            prometheus_url,
            f'sum(rate(fraudshield_sla_breaches_total[{w}]))'
            f' / sum(rate(fraudshield_decisions_total[{w}]))',
        ),
        "decisions_per_min": _query_prometheus(
            prometheus_url,
            f'sum(rate(fraudshield_decisions_total[{w}]))',
        ),
    }


# ── Check logic ───────────────────────────────────────────────────────────────

def offline_check(meta) -> list[str]:
    """Return list of failure reasons from training metrics, empty if healthy."""
    failures = []
    if meta.auc_roc < MIN_AUC_ROC:
        failures.append(
            f"auc_roc={meta.auc_roc:.4f} < floor {MIN_AUC_ROC}"
        )
    if meta.precision < MIN_PRECISION:
        failures.append(
            f"precision={meta.precision:.4f} < floor {MIN_PRECISION}"
        )
    return failures


def online_check(prometheus_url: str, window: int, expected_block_rate_pct: float) -> list[str]:
    """Return list of failure reasons from live Prometheus metrics, empty if healthy."""
    console.print(f"  Querying Prometheus ({prometheus_url}) over last {window}m…")
    metrics = _get_live_metrics(prometheus_url, window)

    if metrics["decisions_per_min"] is None:
        console.print("  [yellow]Prometheus unreachable — skipping online check[/yellow]")
        return []

    total = metrics["decisions_per_min"]
    if total < MIN_DECISIONS_FOR_ONLINE / window:
        console.print(
            f"  [yellow]Too little traffic ({total:.2f} req/min) — skipping online check[/yellow]"
        )
        return []

    failures = []

    block_rate = metrics["block_rate_pct"]
    if block_rate is not None:
        deviation = abs(block_rate - expected_block_rate_pct)
        console.print(
            f"  Block rate: {block_rate:.1f}%  (expected ~{expected_block_rate_pct:.1f}%,  "
            f"deviation {deviation:.1f}pp)"
        )
        if deviation > BLOCK_RATE_MAX_DEVIATION:
            failures.append(
                f"block_rate={block_rate:.1f}% deviates {deviation:.1f}pp "
                f"from expected {expected_block_rate_pct:.1f}%"
            )

    sla = metrics["sla_breach_rate"]
    if sla is not None:
        console.print(f"  SLA breach rate: {sla*100:.2f}%  (max {SLA_BREACH_MAX_RATE*100:.0f}%)")
        if sla > SLA_BREACH_MAX_RATE:
            failures.append(
                f"sla_breach_rate={sla*100:.2f}% > max {SLA_BREACH_MAX_RATE*100:.0f}%"
            )

    return failures


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Post-promotion health check + auto-rollback")
    parser.add_argument("--prometheus-url", default=DEFAULT_PROMETHEUS_URL)
    parser.add_argument("--window-minutes",  type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--output",          default="rollback_result.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate but do not actually rollback",
    )
    args = parser.parse_args()

    result = {
        "run_at":           datetime.utcnow().isoformat(),
        "current_version":  None,
        "current_auc":      None,
        "current_precision": None,
        "previous_version": None,
        "offline_failures": [],
        "online_failures":  [],
        "rollback_triggered": False,
        "rolled_back_to":   None,
        "dry_run":          args.dry_run,
    }

    console.print("\n[bold cyan]FraudShield — Auto-Rollback Health Check[/bold cyan]\n")

    # ── Load registry ──────────────────────────────────────────────────────────
    try:
        registry = LocalFileRegistry()
        _, meta = registry.load("current")
    except FileNotFoundError as exc:
        console.print(f"[red]No champion model found: {exc}[/red]")
        _write_result(result, args.output)
        sys.exit(1)

    result["current_version"]   = meta.version
    result["current_auc"]       = meta.auc_roc
    result["current_precision"] = meta.precision
    result["previous_version"]  = registry.get_previous_version()

    console.print(f"Champion : [cyan]{meta.version}[/cyan]  AUC {meta.auc_roc:.4f}  "
                  f"Precision {meta.precision:.4f}")
    if result["previous_version"]:
        console.print(f"Previous : [dim]{result['previous_version']}[/dim]")
    else:
        console.print("[dim]Previous : none (first model — rollback unavailable)[/dim]")

    # ── Step 1: Offline check ──────────────────────────────────────────────────
    console.print("\n[bold]Step 1/2  Offline metric check…[/bold]")
    offline_failures = offline_check(meta)
    result["offline_failures"] = offline_failures

    if offline_failures:
        for f in offline_failures:
            console.print(f"  [red]✗ {f}[/red]")
    else:
        console.print(f"  [green]✓ AUC {meta.auc_roc:.4f}  Precision {meta.precision:.4f}  — within thresholds[/green]")

    # ── Step 2: Online check ───────────────────────────────────────────────────
    console.print(f"\n[bold]Step 2/2  Online metric check (last {args.window_minutes}m)…[/bold]")
    expected_block_rate = meta.fraud_rate * 100  # training fraud rate ≈ expected block %
    online_failures = online_check(args.prometheus_url, args.window_minutes, expected_block_rate)
    result["online_failures"] = online_failures

    if online_failures:
        for f in online_failures:
            console.print(f"  [red]✗ {f}[/red]")

    # ── Rollback decision ──────────────────────────────────────────────────────
    all_failures = offline_failures + online_failures
    result["rollback_triggered"] = bool(all_failures)

    if not all_failures:
        console.print(f"\n[bold green]✓ Model {meta.version} is healthy — no rollback needed.[/bold green]\n")
        _write_result(result, args.output)
        sys.exit(0)

    # Failures detected
    console.print(f"\n[bold red]⚠  {len(all_failures)} failure(s) detected — rollback required.[/bold red]")

    previous = result["previous_version"]
    if not previous:
        console.print("[yellow]No previous champion to roll back to — leaving current model in place.[/yellow]")
        console.print("[yellow]Manual intervention required.[/yellow]\n")
        _write_result(result, args.output)
        sys.exit(2)

    if args.dry_run:
        console.print(f"[yellow]DRY RUN — would roll back to {previous} but skipping actual promotion.[/yellow]\n")
        result["rolled_back_to"] = previous
        _write_result(result, args.output)
        sys.exit(2)

    rolled_back_to = registry.rollback()
    result["rolled_back_to"] = rolled_back_to
    console.print(f"\n[bold green]✓ Rolled back to {rolled_back_to}[/bold green]\n")
    _write_result(result, args.output)
    sys.exit(2)


def _write_result(result: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    console.print(f"  Result written → [dim]{path}[/dim]")


if __name__ == "__main__":
    main()
