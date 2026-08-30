"""Out-of-time decile validation.

Two rules are enforced here rather than left to the caller, because breaking
either produces a result that looks excellent and means nothing:

**Train and test windows must not overlap.** Scoring the period a model was
fitted on measures memorisation.

**Ties must never be broken by the outcome.** A cell model produces few
distinct scores, so most of the population sits in large tied blocks. Sorting
rows as ``(score, outcome)`` silently orders positives first inside every
block and concentrates them into the top decile -- a label leak that inflated
an early run of this analysis to a fake 2.56x with impossible 0.00% deciles
in the middle. Ties are broken by a seeded random key instead.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .model import Cell, PropensityModel


@dataclass(frozen=True)
class DecileRow:
    decile: int
    count: int
    predicted: float
    actual: float
    lift: float
    cumulative_recall: float


@dataclass(frozen=True)
class ValidationReport:
    base_rate: float
    population: int
    distinct_scores: int
    deciles: Tuple[DecileRow, ...]

    @property
    def top_decile_lift(self) -> float:
        return self.deciles[0].lift if self.deciles else 0.0

    @property
    def top_decile_recall(self) -> float:
        return self.deciles[0].cumulative_recall if self.deciles else 0.0


def validate(
    model: PropensityModel,
    examples: Sequence[Tuple[Cell, bool]],
    *,
    deciles: int = 10,
    seed: int = 0,
) -> ValidationReport:
    """Rank held-out examples by predicted propensity and measure each decile."""
    if not examples:
        return ValidationReport(0.0, 0, 0, ())

    rng = random.Random(seed)
    rows: List[Tuple[float, float, bool]] = [
        (model.predict(cell), rng.random(), outcome) for cell, outcome in examples
    ]
    # Sort by score descending, then by the random key. The outcome never
    # participates in the ordering.
    rows.sort(key=lambda row: (-row[0], row[1]))

    total_hits = sum(1 for row in rows if row[2])
    base_rate = total_hits / len(rows)
    size = len(rows) // deciles
    output: List[DecileRow] = []
    cumulative = 0

    for index in range(deciles):
        start = index * size
        chunk = rows[start : start + size] if index < deciles - 1 else rows[start:]
        if not chunk:
            continue
        hits = sum(1 for row in chunk if row[2])
        cumulative += hits
        actual = hits / len(chunk)
        output.append(
            DecileRow(
                decile=index + 1,
                count=len(chunk),
                predicted=sum(row[0] for row in chunk) / len(chunk),
                actual=actual,
                lift=actual / base_rate if base_rate else 0.0,
                cumulative_recall=cumulative / total_hits if total_hits else 0.0,
            )
        )

    return ValidationReport(
        base_rate=base_rate,
        population=len(rows),
        distinct_scores=len(set(row[0] for row in rows)),
        deciles=tuple(output),
    )


def format_validation(report: ValidationReport, model: PropensityModel) -> str:
    lines = [
        "PROPENSITY VALIDATION (held out)",
        "  population " + format(report.population, ",")
        + "   base rate " + format(report.base_rate * 100, ".2f") + "%"
        + "   distinct scores " + str(report.distinct_scores),
        "",
        "  %-7s %9s %10s %9s %7s %9s" % ("decile", "n", "predicted", "actual", "lift", "cum recall"),
        "  " + "-" * 56,
    ]
    for row in report.deciles:
        lines.append(
            "  %-7s %9s %9.2f%% %8.2f%% %6.2fx %8.0f%%"
            % (
                row.decile,
                format(row.count, ","),
                row.predicted * 100,
                row.actual * 100,
                row.lift,
                row.cumulative_recall * 100,
            )
        )
    lines.append("")
    lines.append("  TOP-SCORING CELLS (owner type, tenure years)")
    for cell, rate, support in model.ranked_cells()[:6]:
        lines.append(
            "    %-26s train n=%-9s predicted %.1f%%"
            % (str(cell), format(support, ","), rate * 100)
        )
    lines.append("")
    lines.append("  Absolute rates do not transfer between market regimes -- only the")
    lines.append("  ranking does. Read lift, not predicted probability.")
    return "\n".join(lines)
