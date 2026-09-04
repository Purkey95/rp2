"""The review loop: queue -> decision -> label -> retrain.

A reviewer's decision does three things at once: it settles the match, it becomes a
label the model trains on, and it is written to the audit trail with who and when.
The queue is ordered by probability so the ten-second decisions come first.
"""

from __future__ import annotations

import csv
import math
from typing import Any, Dict, List, Optional, Tuple

from . import entities, history
from .events import MATCH_CONFIRMED, MATCH_REJECTED
from .normalize.pins import parcel_id, pin_norm
from .resolve.model import LinkModel, load_rules, reliability
from .resolve.resolver import get_match, hydrate, materialize_person
from .store import Store, dumps, loads

ORDERS = ("probability", "uncertainty", "value")


def queue(store: Store, limit: int = 50, kind: Optional[str] = None, order: str = "probability", reviewer: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pending matches, in the order that spends reviewer minutes best.

    probability: easiest first (v1 behaviour). uncertainty: p(1-p) descending, the
    decisions the model is least sure of. value: uncertainty weighted by the parcel's
    assessed value, so a coin-flip on a $600k parcel outranks one on a $60k lot.
    """
    if order not in ORDERS:
        raise ValueError("order must be one of {0}".format(", ".join(ORDERS)))
    sql = "SELECT m.*, p.assessed_value AS _value FROM entity_match m LEFT JOIN parcel p ON p.id = m.right_id WHERE m.status = 'pending'"
    params: List[Any] = []
    if kind:
        sql += " AND m.kind = ?"
        params.append(kind)
    if reviewer:
        sql += " AND m.id NOT IN (SELECT match_id FROM review_decision WHERE reviewer = ? AND decision = 'skip')"
        params.append(reviewer)
    rows = [hydrate(r) for r in store.query(sql, params)]
    for r in rows:
        p = float(r["probability"])
        r["uncertainty"] = round(p * (1 - p), 4)
        value = float(r.pop("_value") or 0.0)
        r["review_value"] = round(r["uncertainty"] * math.log10(value + 10), 4)
    key = {
        "probability": lambda r: (-r["probability"], r["left_id"], r["right_id"]),
        "uncertainty": lambda r: (-r["uncertainty"], r["left_id"], r["right_id"]),
        "value": lambda r: (-r["review_value"], r["left_id"], r["right_id"]),
    }[order]
    rows.sort(key=key)
    return rows[:limit]


def audit_queue(store: Store, limit: int = 50) -> List[Dict[str, Any]]:
    """Rules-confirmed matches sampled for a human look. Their labels measure auto-confirm precision without selection bias."""
    rows = [
        hydrate(r)
        for r in store.query(
            "SELECT * FROM entity_match WHERE status = 'confirmed' AND decided_by = 'rules' AND flags LIKE '%audit_sample%' AND id NOT IN (SELECT match_id FROM review_decision) ORDER BY id LIMIT ?",
            (limit,),
        )
    ]
    return rows


def double_review_queue(store: Store, reviewer: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Matches flagged for a second, independent opinion that this reviewer has not given yet."""
    rows = store.query(
        "SELECT m.* FROM entity_match m WHERE m.flags LIKE '%double_review%' "
        "AND (SELECT COUNT(DISTINCT reviewer) FROM review_decision d WHERE d.match_id = m.id AND d.decision <> 'skip') = 1 "
        "AND NOT EXISTS (SELECT 1 FROM review_decision d WHERE d.match_id = m.id AND d.reviewer = ?) ORDER BY m.id LIMIT ?",
        (reviewer, limit),
    )
    return [hydrate(r) for r in rows]


def groups(store: Store, limit: int = 20, order: str = "value") -> List[Dict[str, Any]]:
    """Estate-centric review: every candidate for one subject on one screen.

    A reviewer deciding "which of these eight David Smiths is the decedent" makes one
    good decision instead of eight poor ones, and the rejections it produces are far
    better negative labels than isolated pair rejections.
    """
    pending = queue(store, limit=10000, order=order)
    seen: List[str] = []
    for m in pending:
        if m["left_id"] not in seen:
            seen.append(m["left_id"])
        if len(seen) >= limit:
            break
    out = []
    for left_id in seen:
        cands = [hydrate(r) for r in store.query("SELECT * FROM entity_match WHERE left_id = ? ORDER BY probability DESC, right_id", (left_id,))]
        first = cands[0]
        left = store.one("SELECT * FROM mention WHERE id = ?", (first["left_mention_id"],)) or {}
        rec = history.current(store, left["source"], left["county"], left["natural_key"]) if left else None
        for c in cands:
            parcel = entities.get_parcel(store, c["right_id"]) if c["right_source"] == "parcel" else None
            right = store.one("SELECT raw_name, address_norm FROM mention WHERE id = ?", (c["right_mention_id"],)) or {}
            c["parcel"] = parcel
            c["owner_name"] = right.get("raw_name")
        out.append(
            {
                "left_id": left_id,
                "subject": rec["payload"] if rec else {},
                "subject_mention": _mention_view(left),
                "candidates": cands,
                "pending": sum(1 for c in cands if c["status"] == "pending"),
            }
        )
    return out


def decide_group(
    store: Store, left_id: str, confirm_right_ids: List[str], reviewer: str, note: Optional[str] = None, reject_others: bool = True
) -> Dict[str, Any]:
    """Confirm the chosen candidates for one subject; reject the remaining pending ones."""
    rows = store.query("SELECT id, right_id, status, decided_by FROM entity_match WHERE left_id = ?", (left_id,))
    if not rows:
        raise KeyError("no candidates for {0}".format(left_id))
    known = {r["right_id"] for r in rows}
    unknown = [r for r in confirm_right_ids if r not in known]
    if unknown:
        raise ValueError("not candidates for {0}: {1}".format(left_id, unknown))
    confirmed, rejected = [], []
    for r in rows:
        if r["right_id"] in confirm_right_ids:
            if r["status"] != "confirmed" or r["decided_by"] != "reviewer":
                decide(store, int(r["id"]), "confirm", reviewer, note)
            confirmed.append(r["right_id"])
        elif reject_others and r["status"] == "pending":
            decide(store, int(r["id"]), "reject", reviewer, note or "not selected in group review")
            rejected.append(r["right_id"])
    return {"left_id": left_id, "confirmed": confirmed, "rejected": rejected}


def agreement_report(store: Store) -> Dict[str, Any]:
    """Where two reviewers decided the same match, how often did they agree?"""
    rows = store.query(
        "SELECT match_id, reviewer, decision FROM review_decision WHERE decision <> 'skip' AND id IN (SELECT MAX(id) FROM review_decision WHERE decision <> 'skip' GROUP BY match_id, reviewer) ORDER BY match_id, id"
    )
    by_match: Dict[int, List[Tuple[str, str]]] = {}
    for r in rows:
        by_match.setdefault(int(r["match_id"]), []).append((r["reviewer"], r["decision"]))
    pairs: Dict[Tuple[str, str], Dict[str, int]] = {}
    total = agree = 0
    disagreements = []
    for match_id, decisions in by_match.items():
        if len(decisions) < 2:
            continue
        for i in range(len(decisions)):
            for j in range(i + 1, len(decisions)):
                (ra, da), (rb, db) = decisions[i], decisions[j]
                key = tuple(sorted((ra, rb)))
                c = pairs.setdefault(key, {"n": 0, "agree": 0})  # type: ignore[arg-type]
                c["n"] += 1
                total += 1
                if da == db:
                    c["agree"] += 1
                    agree += 1
                else:
                    disagreements.append({"match_id": match_id, "reviewers": [ra, rb], "decisions": [da, db]})
    return {
        "double_reviewed": sum(1 for d in by_match.values() if len(d) >= 2),
        "comparisons": total,
        "agreement": round(agree / total, 4) if total else None,
        "by_pair": [{"reviewers": list(k), "n": v["n"], "agreement": round(v["agree"] / v["n"], 4)} for k, v in sorted(pairs.items())],
        "disagreements": disagreements,
    }


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
