"""Run a backtest: reconstruct state at a date, then score against outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from ..parcel.store import ParcelStore
from .reconstruct import OwnerState, owner_states_as_of
from .signals import SIGNALS, Signal


class Outcome(NamedTuple):
    """What happened to a property inside the evaluation window."""

    sold: bool
    arms_length: bool
    estate_sale: bool
    forced_sale: bool
    first_sale_date: Optional[date]


#: Outcome selectors. ``any_sale`` includes non-arms-length transfers, so
#: ``arms_length`` is the honest default for "did this actually trade".
OUTCOMES: Dict[str, str] = {
    "any_sale": "any recorded transfer",
    "arms_length_sale": "qualified arms-length sale",
    "estate_sale": "sold by an estate, heirs, or life estate",
    "forced_sale": "forced sale or auction",
}


def _outcome_value(outcome: Outcome, name: str) -> bool:
    if name == "any_sale":
        return outcome.sold
    if name == "arms_length_sale":
        return outcome.arms_length
    if name == "estate_sale":
        return outcome.estate_sale
    if name == "forced_sale":
        return outcome.forced_sale
    raise KeyError(name)


def outcomes_in_window(store: ParcelStore, start: date, end: date) -> Dict[int, Outcome]:
    """Outcomes for every property with a sale in ``(start, end]``."""
    rows = store.query(
        """
        SELECT property_id,
               MAX(is_arms_length) AS arms,
               MAX(is_estate_sale) AS estate,
               MAX(is_forced_sale) AS forced,
               MIN(sale_date)      AS first_sale
        FROM sales
        WHERE sale_date > ? AND sale_date <= ?
        GROUP BY property_id
        """,
        (start.isoformat(), end.isoformat()),
    )
    return {
        row["property_id"]: Outcome(
            sold=True,
            arms_length=bool(row["arms"]),
            estate_sale=bool(row["estate"]),
            forced_sale=bool(row["forced"]),
            first_sale_date=date.fromisoformat(row["first_sale"]),
        )
        for row in rows
    }


@dataclass(frozen=True)
class SignalResult:
    """Measured performance of one signal against one outcome."""

    signal: str
    outcome: str
    population: int
    signal_size: int
    outcome_total: int
    true_positives: int

    @property
    def base_rate(self) -> float:
        return self.outcome_total / self.population if self.population else 0.0

    @property
    def precision(self) -> float:
        """Share of signalled properties that produced the outcome."""
        return self.true_positives / self.signal_size if self.signal_size else 0.0

    @property
    def recall(self) -> float:
        """Share of all outcomes the signal caught."""
        return self.true_positives / self.outcome_total if self.outcome_total else 0.0

    @property
    def lift(self) -> float:
        """Precision relative to the base rate. 1.0 means no predictive value."""
        base = self.base_rate
        return self.precision / base if base else 0.0

    @property
    def coverage(self) -> float:
        return self.signal_size / self.population if self.population else 0.0


@dataclass(frozen=True)
class BacktestReport:
    as_of: date
    horizon_end: date
    parcels_in_index: int
    states_reconstructed: int
    uncovered: int
    results: Tuple[SignalResult, ...]
    lead_time_days: Dict[str, Optional[float]]

    def for_outcome(self, outcome: str) -> List[SignalResult]:
        chosen = [r for r in self.results if r.outcome == outcome]
        return sorted(chosen, key=lambda r: r.lift, reverse=True)


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def run_backtest(
    store: ParcelStore,
    *,
    as_of: date,
    horizon_end: date,
    signals: Optional[Dict[str, Signal]] = None,
) -> BacktestReport:
    """Measure every signal at ``as_of`` against outcomes through ``horizon_end``.

    The population is properties whose ownership could be reconstructed from a
    sale on or before ``as_of``. Properties with no prior sale are counted as
    uncovered rather than assumed unsold, because their absence is a data gap
    and not evidence.
    """
    chosen_signals = signals if signals is not None else SIGNALS
    states: Dict[int, OwnerState] = owner_states_as_of(store, as_of)
    outcomes = outcomes_in_window(store, as_of, horizon_end)

    parcels_in_index = store.query("SELECT COUNT(*) AS n FROM parcels")[0]["n"]
    population = len(states)

    # Outcome totals are counted over the reconstructed population only, so the
    # base rate and the per-signal precision share a denominator.
    outcome_totals = {name: 0 for name in OUTCOMES}
    for property_id in states:
        outcome = outcomes.get(property_id)
        if outcome is None:
            continue
        for name in OUTCOMES:
            if _outcome_value(outcome, name):
                outcome_totals[name] += 1

    results: List[SignalResult] = []
    lead_times: Dict[str, Optional[float]] = {}

    for signal_name, predicate in chosen_signals.items():
        flagged = [pid for pid, state in states.items() if predicate(state)]
        signal_size = len(flagged)
        per_outcome = {name: 0 for name in OUTCOMES}
        lead_days: List[float] = []
        for property_id in flagged:
            outcome = outcomes.get(property_id)
            if outcome is None:
                continue
            for name in OUTCOMES:
                if _outcome_value(outcome, name):
                    per_outcome[name] += 1
            if outcome.arms_length and outcome.first_sale_date is not None:
                lead_days.append((outcome.first_sale_date - as_of).days)
        for name in OUTCOMES:
            results.append(
                SignalResult(
                    signal=signal_name,
                    outcome=name,
                    population=population,
                    signal_size=signal_size,
                    outcome_total=outcome_totals[name],
                    true_positives=per_outcome[name],
                )
            )
        lead_times[signal_name] = _median(lead_days)

    return BacktestReport(
        as_of=as_of,
        horizon_end=horizon_end,
        parcels_in_index=parcels_in_index,
        states_reconstructed=population,
        uncovered=max(parcels_in_index - population, 0),
        results=tuple(results),
        lead_time_days=lead_times,
    )
