"""The ingest pipeline: fetch -> raw capture -> parse -> version -> mentions -> events.

Idempotent by construction: re-running on the same bytes writes nothing new. Snapshot
sources retire rows that disappeared; incremental sources only accumulate. Every run
records its counts and the field set it saw, which is what quality.py watches for
drift.
"""

from __future__ import annotations

import json
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

from . import entities, events, history, raw
from .normalize.pins import county_norm
from .sources.base import Connector
from .sources.transport import Transport
from .store import Store, dumps


class IngestResult(dict):
    @property
    def ok(self) -> bool:
        return self.get("status") == "ok"


def ingest(store: Store, connector: Connector, transport: Transport, retire_missing: Optional[bool] = None) -> IngestResult:
    spec = connector.spec
    county = county_norm(connector.county)
    wm = store.one("SELECT value FROM watermark WHERE connector = ? AND county = ?", (spec.name, county))
    watermark_before = wm["value"] if wm else None
    run_id = store.insert("ingest_run", {"connector": spec.name, "county": county, "started_at": store.now(), "watermark_before": watermark_before})
    counts = {"rows_seen": 0, "rows_new": 0, "rows_changed": 0, "rows_retired": 0, "rows_failed": 0}
    fields: Set[str] = set()
    seen_keys: Set[Tuple[str, str]] = set()
    counties_seen: Set[str] = {county}
    watermark_after = watermark_before
    domain_events: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        for fetched in connector.fetch(transport, watermark_before):
            capture_id = raw.capture(store, spec.name, county, fetched.body, fetched.url, fetched.content_type)
            try:
                records = connector.parse(fetched.body, fetched.content_type)
            except Exception as exc:  # a parse failure is data, not a crash: recorded, counted, surfaced
                counts["rows_failed"] += 1
                errors.append("parse: {0}".format(exc))
                continue
            for rec in records:
                counts["rows_seen"] += 1
                try:
                    rec = connector.clean(rec)
                    missing = spec.missing_required(rec)
                    if missing:
                        raise ValueError("missing required {0}".format(missing))
                    key = spec.natural_key(rec)
                    # A feed may carry rows for a neighboring county (a Union parcel in a
                    # Mecklenburg pull); the record's own county wins for identity.
                    rec_county = county_norm(rec.get("county")) or county
                    fields.update(rec.keys())
                    seen_keys.add((rec_county, key))
                    counties_seen.add(rec_county)
                    old = history.current(store, spec.name, rec_county, key)
                    old_payload = old["payload"] if old and not old["retired"] else None
                    outcome, version_id, _ = history.write(store, spec.name, rec_county, key, rec, spec.effective_date(rec), capture_id, run_id)
                    if outcome == "unchanged":
                        continue
                    counts["rows_new" if outcome == "new" else "rows_changed"] += 1
                    _index(store, connector, rec_county, key, version_id, rec)
                    domain_events.extend(_emit_domain(store, spec, rec_county, key, version_id, old_payload, rec))
                except Exception as exc:
                    counts["rows_failed"] += 1
                    errors.append("row {0}: {1}".format(counts["rows_seen"], exc))
            if fetched.watermark:
                watermark_after = fetched.watermark
        if retire_missing if retire_missing is not None else spec.mode == "snapshot":
            for c in sorted(counties_seen):
                for key in history.current_keys(store, spec.name, c):
                    if (c, key) not in seen_keys:
                        version_id = history.retire(store, spec.name, c, key, run_id)
                        if version_id:
                            counts["rows_retired"] += 1
                            _retire(store, connector, c, key, version_id)
        if watermark_after is None:
            watermark_after = store.now()
        store.execute(
            "INSERT OR REPLACE INTO watermark (connector, county, value, updated_at) VALUES (?, ?, ?, ?)",
            (spec.name, county, watermark_after, store.now()),
        )
        status = "ok"
        error: Optional[str] = "\n".join(errors) if errors else None
    except Exception:  # transport failure or similar: the run is failed, nothing partial is hidden
        status = "failed"
        error = traceback.format_exc()
        watermark_after = watermark_before

    store.update(
        "ingest_run",
        dict(counts, finished_at=store.now(), status=status, field_set=json.dumps(sorted(fields)), watermark_after=watermark_after, error=error),
        "id = ?",
        (run_id,),
    )
    store.commit()
    return IngestResult(run_id=run_id, connector=spec.name, county=county, status=status, error=error, events=domain_events, **counts)


def _index(store: Store, connector: Connector, county: str, key: str, version_id: int, rec: Dict[str, Any]) -> None:
    spec = connector.spec
    if spec.name == "parcel":
        entities.upsert_parcel(store, county, rec)
    elif spec.name == "address_point":
        entities.index_address_point(store, county, rec)
    entities.index_mentions(store, spec, county, key, version_id, rec)


def _retire(store: Store, connector: Connector, county: str, key: str, version_id: int) -> None:
    spec = connector.spec
    store.execute("UPDATE mention SET current = 0 WHERE source = ? AND county = ? AND natural_key = ?", (spec.name, county, key))
    if spec.name == "parcel":
        pid = entities.retire_parcel(store, county, key)
        _event(store, events.PARCEL_RETIRED, spec.name, county, key, version_id, None, {}, pid)
    elif spec.name == "tax_delinquency":
        pin = key.split("|")[0]
        from .normalize.pins import parcel_id

        _event(store, events.TAX_DELINQUENCY_CLEARED, spec.name, county, key, version_id, None, {}, parcel_id(county, pin))


def _emit_domain(store: Store, spec: Any, county: str, key: str, version_id: int, old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .normalize.pins import parcel_id

    pid = None
    for f in spec.parcel_fields:
        if new.get(f):
            pid = parcel_id(new.get("county") or county, new[f])
            break
    out = []
    for kind, occurred_at, payload in events.run_rules(spec.event_rules, old, new):
        eid = _event(store, kind, spec.name, county, key, version_id, occurred_at, payload, pid)
        out.append({"id": eid, "kind": kind, "natural_key": key, "parcel_id": pid, "occurred_at": occurred_at, "payload": payload})
    return out


def _event(
    store: Store, kind: str, source: str, county: str, key: str, version_id: int, occurred_at: Optional[str], payload: Dict[str, Any], parcel_id_: Optional[str]
) -> int:
    return store.insert(
        "event",
        {
            "kind": kind,
            "source": source,
            "county": county,
            "natural_key": key,
            "record_version_id": version_id,
            "parcel_id": parcel_id_,
            "occurred_at": occurred_at,
            "observed_at": store.now(),
            "payload": dumps(payload),
        },
    )


def ingest_county(
    store: Store, county: str, transport: Transport, sources: Optional[List[str]] = None, profile: str = "default", endpoints: Optional[Dict[str, str]] = None
) -> List[IngestResult]:
    from .sources.base import registry

    results = []
    for connector in registry.connectors(county, profile, endpoints):
        if sources and connector.name not in sources:
            continue
        results.append(ingest(store, connector, transport))
    return results
