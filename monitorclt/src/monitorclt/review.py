"""The review loop: queue -> decision -> label -> retrain.

A reviewer's decision does three things at once: it settles the match, it becomes a
label the model trains on, and it is written to the audit trail with who and when.
The queue is ordered by probability so the ten-second decisions come first.
"""

from __future__ import annotations

import csv
from typing import Any, Dict, List, Optional, Tuple

from . import entities, history
from .events import MATCH_CONFIRMED, MATCH_REJECTED
from .normalize.pins import parcel_id, pin_norm
from .resolve.model import LinkModel, load_rules, reliability
from .resolve.resolver import get_match, hydrate, materialize_person
from .store import Store, dumps, loads


def queue(store: Store, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM entity_match WHERE status = 'pending'"
    params: List[Any] = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY probability DESC, left_id, right_id LIMIT ?"
    params.append(limit)
    return [hydrate(r) for r in store.query(sql, params)]


def detail(store: Store, match_id: int, model: Optional[LinkModel] = None) -> Optional[Dict[str, Any]]:
    """Everything a reviewer needs on one screen: both records, the parcel, the rationale."""
    m = get_match(store, match_id)
    if m is None:
        return None
    left = store.one("SELECT * FROM mention WHERE id = ?", (m["left_mention_id"],)) or {}
    right = store.one("SELECT * FROM mention WHERE id = ?", (m["right_mention_id"],)) or {}
    left_rec = history.current(store, left["source"], left["county"], left["natural_key"]) if left else None
    right_rec = history.current(store, right["source"], right["county"], right["natural_key"]) if right else None
    parcel = entities.get_parcel(store, m["right_id"]) if m["right_source"] == "parcel" else None
    model = model or LinkModel.active(store, load_rules(), m["kind"])
    contributions = model.contributions(m["features"])
    deeds = []
    if parcel:
        deeds = [
            loads(r["payload"], {})
            for r in store.query(
                "SELECT DISTINCT r.payload FROM mention mm JOIN record_version r ON r.id = mm.record_version_id WHERE mm.source = 'deed' AND mm.current = 1 AND mm.parcel_id = ? ORDER BY r.effective_date",
                (m["right_id"],),
            )
        ]
    signals = [
        dict(e, payload=loads(e["payload"], {}))
        for e in store.query(
            "SELECT kind, occurred_at, observed_at, payload FROM event WHERE parcel_id = ? AND kind NOT LIKE 'record_%' ORDER BY id", (m["right_id"],)
        )
    ]
    decisions = store.query("SELECT decision, reviewer, decided_at, note FROM review_decision WHERE match_id = ? ORDER BY id", (match_id,))
    return {
        "match": m,
        "left": {"mention": _mention_view(left), "record": left_rec["payload"] if left_rec else {}},
        "right": {"mention": _mention_view(right), "record": right_rec["payload"] if right_rec else {}, "parcel": parcel},
        "deeds": deeds,
        "parcel_events": signals,
        "contributions": [{"feature": k, "log_odds": round(v, 3)} for k, v in contributions],
        "decisions": decisions,
    }


def _mention_view(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    out["parsed"] = loads(row.get("parsed"), {})
    return out


def decide(store: Store, match_id: int, decision: str, reviewer: str, note: Optional[str] = None, seconds_spent: Optional[float] = None) -> Dict[str, Any]:
    if decision not in ("confirm", "reject", "skip"):
        raise ValueError("decision must be confirm, reject or skip")
    m = get_match(store, match_id)
    if m is None:
        raise KeyError("no match {0}".format(match_id))
    now = store.now()
    store.insert(
        "review_decision", {"match_id": match_id, "decision": decision, "reviewer": reviewer, "decided_at": now, "note": note, "seconds_spent": seconds_spent}
    )
    store.insert(
        "access_log", {"at": now, "actor": reviewer, "action": "review." + decision, "target": "match/{0}".format(match_id), "detail": dumps({"note": note})}
    )
    if decision != "skip":
        status = "confirmed" if decision == "confirm" else "rejected"
        store.update(
            "entity_match",
            {"status": status, "decided_by": "reviewer", "reviewer": reviewer, "reviewed_at": now, "review_note": note, "updated_at": now},
            "id = ?",
            (match_id,),
        )
        if status == "confirmed":
            materialize_person(store, match_id)
        store.execute(
            "INSERT INTO label (kind, left_id, right_id, is_match, origin, labeled_by, labeled_at, note) VALUES (?, ?, ?, ?, 'review', ?, ?, ?) "
            "ON CONFLICT(kind, left_id, right_id) DO UPDATE SET is_match = excluded.is_match, origin = 'review', labeled_by = excluded.labeled_by, labeled_at = excluded.labeled_at, note = excluded.note",
            (m["kind"], m["left_id"], m["right_id"], 1 if status == "confirmed" else 0, reviewer, now, note),
        )
        pid = store.scalar("SELECT parcel_id FROM mention WHERE id = ?", (m["right_mention_id"],))
        store.insert(
            "event",
            {
                "kind": MATCH_CONFIRMED if status == "confirmed" else MATCH_REJECTED,
                "source": "review",
                "county": m["right_id"].split("/", 1)[0],
                "natural_key": str(match_id),
                "parcel_id": pid,
                "observed_at": now,
                "payload": dumps({"match_id": match_id, "left_id": m["left_id"], "right_id": m["right_id"], "reviewer": reviewer}),
            },
        )
    store.commit()
    return get_match(store, match_id) or {}


# ---------------------------------------------------------------- labels ----


def import_labels(store: Store, path: str, kind: str = "estate_case->parcel", origin: str = "import", labeled_by: str = "import") -> Dict[str, Any]:
    """Load a v1-style label CSV (file_number, pin, is_match[, note]); ids resolved from records."""
    estates = _id_table(store, "estate_case")
    parcels = {pin_norm(r["pin"]): r["id"] for r in store.query("SELECT id, pin FROM parcel")}
    parcel_counties: Dict[str, set] = {}
    for r in store.query("SELECT id, pin FROM parcel"):
        parcel_counties.setdefault(pin_norm(r["pin"]), set()).add(r["id"])
    added, problems = 0, []
    with open(path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            left = _resolve(row.get("file_number"), estates, "file number", i)
            pin = row.get("pin") or ""
            if "/" in pin:
                right: Optional[str] = parcel_id(*pin.split("/", 1))
            else:
                found = parcel_counties.get(pin_norm(pin), set())
                if len(found) > 1:
                    problems.append("line {0}: PIN {1!r} exists in {2} counties; write COUNTY/PIN".format(i, pin, len(found)))
                    continue
                right = next(iter(found), None) or parcels.get(pin_norm(pin))
            if isinstance(left, str) and left.startswith("!"):
                problems.append(left[1:])
                continue
            if not left or not right:
                problems.append("line {0}: could not resolve {1!r} / {2!r} to records".format(i, row.get("file_number"), pin))
                continue
            is_match = 1 if str(row.get("is_match", "")).strip().upper() in ("1", "TRUE", "T", "YES", "Y", "MATCH") else 0
            store.execute(
                "INSERT INTO label (kind, left_id, right_id, is_match, origin, labeled_by, labeled_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, left_id, right_id) DO UPDATE SET is_match = excluded.is_match, note = excluded.note",
                (kind, left, right, is_match, origin, labeled_by, store.now(), row.get("note")),
            )
            added += 1
    store.commit()
    return {"added": added, "problems": problems}


def _id_table(store: Store, source: str) -> Dict[str, set]:
    table: Dict[str, set] = {}
    for r in store.query("SELECT county, natural_key FROM record_version WHERE source = ? AND superseded_at IS NULL", (source,)):
        table.setdefault(_norm(r["natural_key"]), set()).add("{0}/{1}".format(r["county"], r["natural_key"]))
    return table


def _norm(v: Any) -> str:
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def _resolve(value: Optional[str], table: Dict[str, set], what: str, line: int) -> Optional[str]:
    if value and "/" in value:
        return value
    found = table.get(_norm(value), set())
    if len(found) > 1:
        return "!line {0}: {1} {2!r} exists in {3} counties; write COUNTY/{2}".format(line, what, value, len(found))
    return next(iter(found), None)


def labels(store: Store, kind: str = "estate_case->parcel") -> List[Dict[str, Any]]:
    return store.query("SELECT * FROM label WHERE kind = ? ORDER BY left_id, right_id", (kind,))


def training_rows(store: Store, kind: str = "estate_case->parcel") -> Tuple[List[Tuple[Dict[str, float], int]], List[Dict[str, Any]]]:
    """(features, y) for every label that has a scored candidate; labels with none are 'blocked out'."""
    rows: List[Tuple[Dict[str, float], int]] = []
    unscored: List[Dict[str, Any]] = []
    for lab in labels(store, kind):
        m = store.one("SELECT features FROM entity_match WHERE kind = ? AND left_id = ? AND right_id = ?", (kind, lab["left_id"], lab["right_id"]))
        if m is None:
            unscored.append(lab)
            continue
        rows.append((loads(m["features"], {}), int(lab["is_match"])))
    return rows, unscored


def train(store: Store, rules: Optional[Dict[str, Any]] = None, kind: str = "estate_case->parcel", activate: bool = True) -> Dict[str, Any]:
    rules = rules or load_rules()
    rows, unscored = training_rows(store, kind)
    seed = LinkModel.seed(rules, kind)
    model = LinkModel(seed.weights, seed.intercept, None, kind)
    cfg = rules.get("training", {})
    metrics = model.train(rows, seed, l2=cfg.get("l2_to_seed", 1.0), lr=cfg.get("learning_rate", 0.1), epochs=cfg.get("epochs", 400))
    pairs = [(model.predict(f), y) for f, y in rows]
    metrics["reliability"] = reliability(pairs)
    metrics["unscored_labels"] = len(unscored)
    version_id = model.save(store, len(rows), metrics, activate=activate) if rows else None
    return {"model": model.to_dict(), "metrics": metrics, "version_id": version_id}
