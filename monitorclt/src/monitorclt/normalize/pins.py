"""Parcel identifiers and lineage.

PINs are printed with dashes, dots and spaces depending on which county system
emitted them; the normalized form is alphanumerics only. Parcels also split, merge
and get renumbered, and a join on the printed PIN silently loses history across a
re-plat. `resolve_current` follows the lineage table to whatever the parcel is today.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from ..store import Store


def pin_norm(pin: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (pin or "").upper())


def county_norm(county: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (county or "").upper().replace("COUNTY", "")).strip()


def parcel_id(county: Optional[str], pin: Optional[str]) -> str:
    return "{0}/{1}".format(county_norm(county), pin_norm(pin))


def record_lineage(store: Store, parent_id: str, child_id: str, kind: str, effective_date: Optional[str] = None, source: Optional[str] = None) -> None:
    store.execute(
        "INSERT OR REPLACE INTO parcel_lineage (parent_id, child_id, kind, effective_date, source) VALUES (?, ?, ?, ?, ?)",
        (parent_id, child_id, kind, effective_date, source),
    )
    if kind in ("renumber", "merge"):
        store.update("parcel", {"superseded_by": child_id}, "id = ? AND superseded_by IS NULL", (parent_id,))


def children(store: Store, parent_id: str) -> List[Dict[str, str]]:
    return store.query("SELECT child_id, kind, effective_date FROM parcel_lineage WHERE parent_id = ? ORDER BY child_id", (parent_id,))


def parents(store: Store, child_id: str) -> List[Dict[str, str]]:
    return store.query("SELECT parent_id, kind, effective_date FROM parcel_lineage WHERE child_id = ? ORDER BY parent_id", (child_id,))


def resolve_current(store: Store, parcel_id_: str, _seen: Optional[Set[str]] = None) -> List[str]:
    """Follow renumbers, merges and splits to the parcel ids that exist today."""
    seen = _seen or set()
    if parcel_id_ in seen:
        return []
    seen.add(parcel_id_)
    kids = children(store, parcel_id_)
    if not kids:
        return [parcel_id_]
    out: List[str] = []
    for kid in kids:
        out.extend(resolve_current(store, kid["child_id"], seen))
    return sorted(set(out))


def ancestry(store: Store, parcel_id_: str, _seen: Optional[Set[str]] = None) -> List[str]:
    """Every parcel id whose history flows into this one (for pulling old deeds forward)."""
    seen = _seen or set()
    if parcel_id_ in seen:
        return []
    seen.add(parcel_id_)
    out = [parcel_id_]
    for p in parents(store, parcel_id_):
        out.extend(ancestry(store, p["parent_id"], seen))
    return sorted(set(out))
