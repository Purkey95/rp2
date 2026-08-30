"""Render a backtest as a table an operator can argue with."""

from __future__ import annotations

from typing import List

from .harness import BacktestReport, OUTCOMES
from .signals import NOT_BACKTESTABLE


def _percent(value: float) -> str:
    return format(value * 100, ".2f") + "%"


def format_report(report: BacktestReport, *, outcomes: List[str] = None) -> str:
    """Format a report, most predictive signal first within each outcome."""
    chosen = outcomes if outcomes is not None else list(OUTCOMES)
    lines: List[str] = []
    lines.append("BACKTEST  as of " + report.as_of.isoformat() + "  ->  " + report.horizon_end.isoformat())
    lines.append("=" * 96)
    lines.append(
        "population: "
        + format(report.states_reconstructed, ",")
        + " properties with reconstructable ownership"
    )
    lines.append(
        "uncovered:  "
        + format(report.uncovered, ",")
        + " of "
        + format(report.parcels_in_index, ",")
        + " indexed parcels had no sale on or before the as-of date"
    )
    lines.append("")

    for outcome in chosen:
        results = report.for_outcome(outcome)
        if not results:
            continue
        base = results[0].base_rate
        lines.append("OUTCOME: " + outcome + "  (" + OUTCOMES[outcome] + ")")
        lines.append(
            "  base rate "
            + _percent(base)
            + "  ("
            + format(results[0].outcome_total, ",")
            + " of "
            + format(results[0].population, ",")
            + ")"
        )
        lines.append(
            "  %-26s %10s %8s %10s %9s %8s %10s"
            % ("signal", "flagged", "cover", "hits", "precision", "lift", "recall")
        )
        lines.append("  " + "-" * 88)
        for result in results:
            lines.append(
                "  %-26s %10s %8s %10s %9s %8s %10s"
                % (
                    result.signal,
                    format(result.signal_size, ","),
                    _percent(result.coverage),
                    format(result.true_positives, ","),
                    _percent(result.precision),
                    format(result.lift, ".2f") + "x",
                    _percent(result.recall),
                )
            )
        lines.append("")

    lines.append("MEDIAN LEAD TIME to first arms-length sale (days from as-of)")
    for signal, median in sorted(
        report.lead_time_days.items(), key=lambda kv: (kv[1] is None, kv[1])
    ):
        lines.append("  %-26s %s" % (signal, "-" if median is None else format(median, ".0f")))
    lines.append("")

    lines.append("NOT BACKTESTABLE FROM CURRENT DATA")
    for name, reason in NOT_BACKTESTABLE:
        lines.append("  " + name)
        lines.append("      " + reason)
    lines.append("")

    lines.append("READING THIS")
    lines.append("  lift 1.00x means the signal is no better than picking at random from")
    lines.append("  the population. A signal only earns its complexity by beating both the")
    lines.append("  base rate and the simpler signals above it.")
    lines.append("")
    lines.append("  A single as-of date is not a result. Rare outcomes especially (forced")
    lines.append("  sales, estate sales) swing by more than their own effect size between")
    lines.append("  windows, so run several dates and believe only what replicates.")
    lines.append("  Where a signal's hit count is in the tens, assume noise until shown")
    lines.append("  otherwise.")
    return "\n".join(lines)
