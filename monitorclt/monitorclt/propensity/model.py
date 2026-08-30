"""A transparent cell-based propensity model.

Deliberately not a black box. The model is a lookup table over
``(owner_type, tenure_bucket)`` cells, each holding the historical rate at
which properties in that cell transacted over the following window. Every
prediction can be explained by naming its cell and that cell's training
support, which matters more here than squeezing out accuracy: the point of
the exercise is to find out *what* is predictive, and a model you cannot
interrogate cannot tell you that.

Smoothing pulls thin cells toward the population prior so a cell with four
training rows cannot outrank one with forty thousand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from ..backtest.reconstruct import OwnerState

Cell = Tuple[str, str]

#: Tenure buckets in years. Chosen to be readable rather than optimal; the
#: measured effect is monotone enough that bucket edges do not carry the result.
TENURE_BUCKETS: Sequence[Tuple[float, str]] = (
    (3.0, "0-3"),
    (7.0, "3-7"),
    (12.0, "7-12"),
    (20.0, "12-20"),
    (30.0, "20-30"),
)
TENURE_BUCKET_TOP = "30+"

DEFAULT_SMOOTHING = 20.0


def tenure_bucket(years: float) -> str:
    for edge, label in TENURE_BUCKETS:
        if years < edge:
            return label
    return TENURE_BUCKET_TOP


def cell_for(state: OwnerState) -> Cell:
    return (state.owner_type, tenure_bucket(state.tenure_years))


@dataclass(frozen=True)
class PropensityModel:
    """Fitted cell rates plus the prior used for unseen or thin cells."""

    rates: Dict[Cell, float]
    support: Dict[Cell, int]
    prior: float
    smoothing: float

    def predict(self, cell: Cell) -> float:
        return self.rates.get(cell, self.prior)

    def predict_state(self, state: OwnerState) -> float:
        return self.predict(cell_for(state))

    def ranked_cells(self, minimum_support: int = 0) -> List[Tuple[Cell, float, int]]:
        rows = [
            (cell, rate, self.support[cell])
            for cell, rate in self.rates.items()
            if self.support[cell] >= minimum_support
        ]
        return sorted(rows, key=lambda row: -row[1])

    @classmethod
    def fit(
        cls, examples: Iterable[Tuple[Cell, bool]], *, smoothing: float = DEFAULT_SMOOTHING
    ) -> "PropensityModel":
        counts: Dict[Cell, int] = {}
        hits: Dict[Cell, int] = {}
        for cell, outcome in examples:
            counts[cell] = counts.get(cell, 0) + 1
            hits[cell] = hits.get(cell, 0) + (1 if outcome else 0)
        total = sum(counts.values())
        if not total:
            return cls({}, {}, 0.0, smoothing)
        prior = sum(hits.values()) / total
        rates = {
            cell: (hits[cell] + smoothing * prior) / (counts[cell] + smoothing)
            for cell in counts
        }
        return cls(rates=rates, support=counts, prior=prior, smoothing=smoothing)
