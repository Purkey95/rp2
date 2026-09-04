"""Domain events: what a change in a source record *means*.

history.py says "field X went from A to B". A source adapter says "the owner of this
parcel changed" or "an estate was opened". Rules here are small pure functions
(old_payload, new_payload) -> [(kind, occurred_at, payload)], declared per source in
the county plugin, so the change stream stays explainable and the catalog of event
kinds is a list you can read.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .clock import date_only

Event = Tuple[str, Optional[str], Dict[str, Any]]

# Catalog. Watchlists filter on these names, so they are part of the public contract.
ESTATE_OPENED = "estate_opened"
ESTATE_STATUS_CHANGED = "estate_status_changed"
ESTATE_CLOSED = "estate_closed"
OWNER_CHANGED = "owner_changed"
ASSESSED_VALUE_CHANGED = "assessed_value_changed"
MAILING_ADDRESS_CHANGED = "mailing_address_changed"
DEED_RECORDED = "deed_recorded"
FORECLOSURE_FILED = "foreclosure_filed"
FORECLOSURE_HEARING_SET = "foreclosure_hearing_set"
FORECLOSURE_STATUS_CHANGED = "foreclosure_status_changed"
TAX_DELINQUENT = "tax_delinquent"
TAX_DELINQUENCY_CLEARED = "tax_delinquency_cleared"
CODE_CASE_OPENED = "code_case_opened"
CODE_CASE_CLOSED = "code_case_closed"
MATCH_CONFIRMED = "match_confirmed"
MATCH_REJECTED = "match_rejected"
PARCEL_RETIRED = "parcel_retired"
ENTITY_REGISTERED = "entity_registered"
ENTITY_STATUS_CHANGED = "entity_status_changed"
ENTITY_DISSOLVED = "entity_dissolved"
OUTCOME_NOT_IN_ESTATE = "outcome_not_in_estate"
OUTCOME_ALREADY_SOLD = "outcome_already_sold"

ALL_KINDS = [
    ESTATE_OPENED,
    ESTATE_STATUS_CHANGED,
    ESTATE_CLOSED,
    OWNER_CHANGED,
    ASSESSED_VALUE_CHANGED,
    MAILING_ADDRESS_CHANGED,
    DEED_RECORDED,
    FORECLOSURE_FILED,
    FORECLOSURE_HEARING_SET,
    FORECLOSURE_STATUS_CHANGED,
    TAX_DELINQUENT,
    TAX_DELINQUENCY_CLEARED,
    CODE_CASE_OPENED,
    CODE_CASE_CLOSED,
    MATCH_CONFIRMED,
    MATCH_REJECTED,
    PARCEL_RETIRED,
]


def on_create(kind: str, date_field: Optional[str] = None, fields: Tuple[str, ...] = ()) -> Callable[..., List[Event]]:
    """Emit `kind` the first time a record is seen."""

    def rule(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Event]:
        if old is not None:
            return []
        return [(kind, date_only(new.get(date_field)) if date_field else None, {f: new.get(f) for f in fields})]

    return rule


def on_field_change(field: str, kind: str, date_field: Optional[str] = None) -> Callable[..., List[Event]]:
    """Emit `kind` whenever `field` moves, carrying from/to."""

    def rule(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Event]:
        if old is None or old.get(field) == new.get(field):
            return []
        return [(kind, date_only(new.get(date_field)) if date_field else None, {"field": field, "from": old.get(field), "to": new.get(field)})]

    return rule


def on_status(field: str, closed_values: Tuple[str, ...], changed_kind: str, closed_kind: str, date_field: Optional[str] = None) -> Callable[..., List[Event]]:
    """Status transitions: any change emits changed_kind; a move into closed_values also emits closed_kind."""

    def rule(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Event]:
        if old is None or old.get(field) == new.get(field):
            return []
        out: List[Event] = [(changed_kind, date_only(new.get(date_field)) if date_field else None, {"from": old.get(field), "to": new.get(field)})]
        if str(new.get(field) or "").upper() in closed_values:
            out.append((closed_kind, date_only(new.get(date_field)) if date_field else None, {"from": old.get(field), "to": new.get(field)}))
        return out

    return rule


def on_value_change(field: str, kind: str, min_pct: float = 0.0) -> Callable[..., List[Event]]:
    """Numeric change above a relative threshold (e.g. assessed value reappraisal)."""

    def rule(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Event]:
        if old is None:
            return []
        try:
            a, b = float(old.get(field) or 0), float(new.get(field) or 0)
        except (TypeError, ValueError):
            return []
        if a == b:
            return []
        pct = abs(b - a) / a if a else 1.0
        if pct < min_pct:
            return []
        return [(kind, None, {"field": field, "from": a, "to": b, "pct": round(pct, 4)})]

    return rule


def run_rules(rules: List[Callable[..., List[Event]]], old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Event]:
    out: List[Event] = []
    for rule in rules:
        out.extend(rule(old, new))
    return out
