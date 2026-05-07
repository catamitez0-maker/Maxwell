"""
Rich terminal dashboard for real-time funnel monitoring.

Displays: request counts, per-layer block stats, QPS, entropy thresholds,
FLOPs metering, circuit breaker, load, and uptime.
"""

from __future__ import annotations

from rich import box
from rich.table import Table

from .models import FunnelStats

__all__ = ["create_dashboard"]


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{part / total * 100:.1f}%"


def create_dashboard(stats: FunnelStats) -> Table:
    """Build a Rich table showing live funnel metrics."""
    table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
        title="⚡ Maxwell Protocol",
        title_style="bold cyan",
        min_width=58,
    )
    table.add_column("Metric", style="cyan", min_width=32)
    table.add_column("Value", justify="right", style="green", min_width=14)
    table.add_column("Share", justify="right", style="dim", min_width=8)

    total = stats.total_requests
    blocked = stats.total_blocked

    # ── Overview
    table.add_row("Total Requests", f"{total:,}", "")
    table.add_row("QPS", f"{stats.qps:.1f}", "", style="bold white")

    # ── Pruning Layers
    table.add_row("─── Pruning Funnel ─────", "", "", style="dim")
    table.add_row(
        "  L1 Bloom Blocked", f"{stats.bloom_blocked:,}",
        _pct(stats.bloom_blocked, blocked), style="yellow",
    )
    table.add_row(
        "  L2 Regex Blocked", f"{stats.regex_blocked:,}",
        _pct(stats.regex_blocked, blocked), style="yellow",
    )
    table.add_row(
        "  L3 Entropy Blocked", f"{stats.entropy_blocked:,}",
        _pct(stats.entropy_blocked, blocked), style="yellow",
    )
    table.add_row(
        "  L4 Oracle (FLOPs) Blocked", f"{stats.oracle_blocked:,}",
        _pct(stats.oracle_blocked, blocked), style="magenta",
    )
    table.add_row(
        "  L5 Repetition Blocked", f"{stats.repetition_blocked:,}",
        _pct(stats.repetition_blocked, blocked), style="magenta",
    )
    table.add_row(
        "  Circuit Breaker Blocked", f"{stats.circuit_blocked:,}",
        _pct(stats.circuit_blocked, blocked),
        style="bold white on red" if stats.is_circuit_open else "dim",
    )
    table.add_row(
        "  Passed → Engine", f"{stats.passed_to_engine:,}",
        _pct(stats.passed_to_engine, total), style="bold green",
    )

    # ── Oracle & System
    table.add_row("─── Oracle & System ────", "", "", style="dim")
    table.add_row("  Total FLOPs Metered", stats.flops_display, "", style="bold blue")
    table.add_row(
        "  Circuit Breaker",
        "[red]■ OPEN" if stats.is_circuit_open else "[green]■ CLOSED",
        "",
    )

    load_color = (
        "green" if stats.current_load < 0.5
        else ("yellow" if stats.current_load < 0.8 else "red")
    )
    table.add_row("  Engine Load", f"{stats.current_load * 100:.1f}%", "", style=load_color)
    table.add_row("  Pruning Rate", f"{stats.pruning_rate:.2f}%", "", style="bold red")
    table.add_row(
        "  Entropy Threshold", f"[{stats.entropy_low:.1f}, {stats.entropy_high:.1f}]",
        "", style="magenta",
    )
    table.add_row("  Active Streams", f"{stats.active_streams}", "")
    table.add_row("  Uptime", f"{stats.uptime:.1f}s", "", style="dim")

    return table
