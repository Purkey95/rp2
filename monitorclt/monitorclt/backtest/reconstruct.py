"""Reconstruct owner state as of a past date from the sales chain.

Why this is necessary: the CAMA parcel layer describes the **current** state.
Its owner name, owner type and last-sale date already reflect any sale we are
trying to predict, so scoring a historical parcel from CAMA fields and then
asking whether it sold is circular -- the classic feature leak. The sales
history is the only source that can be truncated at a date.

The reconstruction is simple but exact: as of date T, the owner of a property
is the **grantee of its most recent sale on or before T**, and its tenure is
T minus that sale's date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

from ..normalize.owner import DECEDENT_TYPES, OwnerType
from ..parcel.store import ParcelStore


@dataclass(frozen=True)
class OwnerState:
    """What was known about a property's ownership as of a past date."""

    property_id: int
    as_of: date
    owner: Optional[str]
    owner_type: str
    owned_since: date
    prior_sale_count: int

    @property
    def tenure_years(self) -> float:
        return (self.as_of - self.owned_since).days / 365.25

    @property
    def is_individual(self) -> bool:
        return self.owner_type in (OwnerType.PERSON, OwnerType.COUPLE)

    @property
    def indicates_decedent(self) -> bool:
        return self.owner_type in DECEDENT_TYPES


def owner_states_as_of(store: ParcelStore, as_of: date) -> Dict[int, OwnerState]:
    """Owner state for every property with at least one sale on or before ``as_of``.

    Properties with no prior sale are absent rather than guessed at; the
    harness reports them as uncovered.
    """
    # MIN(rowid) breaks ties deterministically when a property records more
    # than one transfer on its most recent date (multi-parcel conveyances do
    # this), so a rerun cannot silently pick a different owner.
    rows = store.query(
        """
        SELECT s.property_id, s.grantee, s.grantee_type, s.sale_date, c.prior_count
        FROM sales s
        JOIN (
            SELECT property_id, MAX(sale_date) AS latest, MIN(rowid) AS keep
            FROM sales WHERE sale_date <= ?
            GROUP BY property_id
        ) latest_sale
          ON latest_sale.property_id = s.property_id AND latest_sale.latest = s.sale_date
        JOIN (
            SELECT property_id, COUNT(*) AS prior_count
            FROM sales WHERE sale_date <= ? GROUP BY property_id
        ) c ON c.property_id = s.property_id
        GROUP BY s.property_id
        """,
        (as_of.isoformat(), as_of.isoformat()),
    )
    states: Dict[int, OwnerState] = {}
    for row in rows:
        states[row["property_id"]] = OwnerState(
            property_id=row["property_id"],
            as_of=as_of,
            owner=row["grantee"],
            owner_type=row["grantee_type"] or OwnerType.UNKNOWN,
            owned_since=date.fromisoformat(row["sale_date"]),
            prior_sale_count=int(row["prior_count"]),
        )
    return states
