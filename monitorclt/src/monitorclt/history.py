"""Bitemporal, append-only source history and the change stream it produces.

A record is identified by (source, county, natural_key). Writing a payload that
differs from the current version closes the current version (superseded_at) and
opens a new one (observed_at). The source's own date (filing date, recording date,
assessment year) is kept separately as effective_date, so both questions have
answers: "what did we know on Tuesday?" and "what was true on Tuesday?".

Every transition emits a record_created / record_changed / record_retired event
with the field-level diff. Domain events (estate_opened, owner_changed, ...) are
layered on top by the source adapter in events.py.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .store import Store, dumps, loads

RECORD_CREATED = "record_created"
RECORD_CHANGED = "record_changed"
RECORD_RETIRED = "record_retired"


def payload_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def diff(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Field-level diff: {field: {"from": x, "to": y}} for every field that moved."""
    old = old or {}
    new = new or {}
    out: Dict[str, Dict[str, Any]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            out[key] = {"from": old.get(key), "to": new.get(key)}
    return out


def _emit(store: Store, kind: str, source: str, county: str, natural_key: str, version_id: int, occurred_at: Optional[str], payload: Dict[str, Any]) -> int:
    return store.insert(
        "event",
        {
            "kind": kind,
            "source": source,
            "county": county,
            "natural_key": natural_key,
            "record_version_id": version_id,
            "occurred_at": occurred_at,
            "observed_at": store.now(),
            "payload": dumps(payload),
        },
    )


def current(store: Store, source: str, county: str, natural_key: str) -> Optional[Dict[str, Any]]:
    row = store.one(
        "SELECT * FROM record_version WHERE source = ? AND county = ? AND natural_key = ? AND superseded_at IS NULL",
        (source, county, natural_key),
    )
    return _hydrate(row)


def _hydrate(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    row["payload"] = loads(row["payload"], {})
    row["retired"] = bool(row["retired"])
    return row


def write(
    store: Store,
    source: str,
    county: str,
    natural_key: str,
    payload: Dict[str, Any],
    effective_date: Optional[str] = None,
    raw_capture_id: Optional[int] = None,
    ingest_run_id: Optional[int] = None,
) -> Tuple[str, int, Dict[str, Dict[str, Any]]]:
    """Record a version. Returns (outcome, version_id, field_diff).

    outcome is 'new', 'changed', or 'unchanged'. Unchanged writes touch nothing; the
    existing version keeps its observed_at, because we did not learn anything new.
    """
    digest = payload_hash(payload)
    now = store.now()
    cur = current(store, source, county, natural_key)
    if cur is not None and cur["content_hash"] == digest and not cur["retired"]:
        return "unchanged", int(cur["id"]), {}

    version_no = 1 if cur is None else int(cur["version_no"]) + 1
    if cur is not None:
        store.update("record_version", {"superseded_at": now}, "id = ?", (cur["id"],))
    version_id = store.insert(
        "record_version",
        {
            "source": source,
            "county": county,
            "natural_key": natural_key,
            "version_no": version_no,
            "observed_at": now,
            "superseded_at": None,
            "effective_date": effective_date,
            "retired": 0,
            "content_hash": digest,
            "payload": dumps(payload),
            "raw_capture_id": raw_capture_id,
            "ingest_run_id": ingest_run_id,
        },
    )
    if cur is None:
        _emit(store, RECORD_CREATED, source, county, natural_key, version_id, effective_date, {"fields": sorted(payload)})
        return "new", version_id, {k: {"from": None, "to": v} for k, v in payload.items()}
    changes = diff(cur["payload"], payload)
    _emit(store, RECORD_CHANGED, source, county, natural_key, version_id, effective_date, {"diff": changes, "was_retired": cur["retired"]})
    return "changed", version_id, changes


def retire(store: Store, source: str, county: str, natural_key: str, ingest_run_id: Optional[int] = None) -> Optional[int]:
    """Mark a record as no longer present in its source (snapshot sources only)."""
    cur = current(store, source, county, natural_key)
    if cur is None or cur["retired"]:
        return None
    now = store.now()
    store.update("record_version", {"superseded_at": now}, "id = ?", (cur["id"],))
    version_id = store.insert(
        "record_version",
        {
            "source": source,
            "county": county,
            "natural_key": natural_key,
            "version_no": int(cur["version_no"]) + 1,
            "observed_at": now,
            "superseded_at": None,
            "effective_date": cur["effective_date"],
            "retired": 1,
            "content_hash": cur["content_hash"],
            "payload": dumps(cur["payload"]),
            "raw_capture_id": cur["raw_capture_id"],
            "ingest_run_id": ingest_run_id,
        },
    )
    _emit(store, RECORD_RETIRED, source, county, natural_key, version_id, None, {})
    return version_id


def current_records(store: Store, source: str, county: Optional[str] = None, include_retired: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM record_version WHERE source = ? AND superseded_at IS NULL"
    params: List[Any] = [source]
    if county:
        sql += " AND county = ?"
        params.append(county)
    if not include_retired:
        sql += " AND retired = 0"
    sql += " ORDER BY county, natural_key"
    return [_hydrate(r) for r in store.query(sql, params)]  # type: ignore[misc]


def current_keys(store: Store, source: str, county: str) -> List[str]:
    rows = store.query(
        "SELECT natural_key FROM record_version WHERE source = ? AND county = ? AND superseded_at IS NULL AND retired = 0",
        (source, county),
    )
    return [r["natural_key"] for r in rows]


def versions(store: Store, source: str, county: str, natural_key: str) -> List[Dict[str, Any]]:
    rows = store.query(
        "SELECT * FROM record_version WHERE source = ? AND county = ? AND natural_key = ? ORDER BY version_no",
        (source, county, natural_key),
    )
    return [_hydrate(r) for r in rows]  # type: ignore[misc]


def as_of(store: Store, source: str, county: str, natural_key: str, system_time: str) -> Optional[Dict[str, Any]]:
    """What we knew at system_time: the version observed at or before it and not yet superseded."""
    row = store.one(
        "SELECT * FROM record_version WHERE source = ? AND county = ? AND natural_key = ? "
        "AND observed_at <= ? AND (superseded_at IS NULL OR superseded_at > ?) ORDER BY version_no DESC LIMIT 1",
        (source, county, natural_key, system_time, system_time),
    )
    return _hydrate(row)


def effective_as_of(store: Store, source: str, county: str, natural_key: str, valid_date: str) -> Optional[Dict[str, Any]]:
    """What the source asserted was true on valid_date: the latest version whose effective_date <= valid_date."""
    row = store.one(
        "SELECT * FROM record_version WHERE source = ? AND county = ? AND natural_key = ? "
        "AND effective_date IS NOT NULL AND effective_date <= ? ORDER BY effective_date DESC, version_no DESC LIMIT 1",
        (source, county, natural_key, valid_date),
    )
    return _hydrate(row)


def events_since(
    store: Store, since: Optional[str] = None, kinds: Optional[Iterable[str]] = None, after_id: int = 0, limit: int = 1000
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM event WHERE id > ?"
    params: List[Any] = [after_id]
    if since:
        sql += " AND observed_at >= ?"
        params.append(since)
    if kinds:
        kinds = list(kinds)
        sql += " AND kind IN ({0})".format(", ".join("?" for _ in kinds))
        params.extend(kinds)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    rows = store.query(sql, params)
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows
