"""Build train/test example sets from the store."""

from __future__ import annotations

from datetime import date
from typing import List, Tuple

from ..backtest.harness import outcomes_in_window
from ..backtest.reconstruct import owner_states_as_of
from ..parcel.store import ParcelStore
from .model import Cell, cell_for


def build_examples(
    store: ParcelStore, *, as_of: date, horizon_end: date, arms_length_only: bool = True
) -> List[Tuple[Cell, bool]]:
    """``(cell, did_it_transact)`` for every property with reconstructable state.

    The outcome defaults to a qualified arms-length sale rather than any
    transfer, because ``any_sale`` includes multi-parcel conveyances and
    intra-family transfers that are not market events.
    """
    states = owner_states_as_of(store, as_of)
    outcomes = outcomes_in_window(store, as_of, horizon_end)
    examples: List[Tuple[Cell, bool]] = []
    for property_id, state in states.items():
        outcome = outcomes.get(property_id)
        if outcome is None:
            transacted = False
        else:
            transacted = outcome.arms_length if arms_length_only else outcome.sold
        examples.append((cell_for(state), transacted))
    return examples
