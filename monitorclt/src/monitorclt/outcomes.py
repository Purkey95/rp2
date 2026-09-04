"""Outreach outcomes: what happened after a lead left the system.

This closes the loop. A decline becomes a suppression the same second it is
recorded. "Not in the estate" and "already sold" become negative signals on the
parcel, and the conversion report says which signals and which evidence actually
produced conversations, so the hand-set weights can be argued with numbers.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from . import entities, history, policy
from .ids import stable_id
from .resolve.resolver import get_match
from .store import Store, dumps

OUTCOMES = ("reached", "no_response", "declined", "not_in_estate", "already_sold", "under_contract", "closed", "invalid_contact")
POSITIVE = ("reached", "under_contract", "closed")
NEGATIVE_PARCEL = {"not_in_estate": "outcome_not_in_estate", "already_sold": "outcome_already_sold"}


def record_outcome(
    store: Store, outcome: str, recorded_by: str, match_id: Optional[int] = None, lead_id: Optional[str] = None, note: Optional[str] = None
) -> Dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of {0}".format(", ".join(OUTCOMES)))
    if match_id is None and lead_id:
        match_id = _match_for_lead(store, lead_id)
    if match_id is None:
        raise KeyError("no match for lead {0!r}".format(lead_id))
    m = get_match(store, match_id)
    if m is None:
        raise KeyError("no match {0}".format(match_id))
    lead = lead_id or stable_id(m["left_id"], m["right_id"])
    now = store.now()
    oid = store.insert("outcome", {"match_id": match_id, "lead_id": lead, "outcome": outcome, "recorded_by": recorded_by, "recorded_at": now, "note": note})
    store.insert(
        "access_log",
        {
            "at": now,
            "actor": recorded_by,
            "action": "outcome." + outcome,
            "target": "match/{0}".format(match_id),
            "detail": dumps({"lead_id": lead, "note": note}),
        },
    )
    effects: List[str] = []

    if outcome in ("declined", "invalid_contact"):
        left = store.one("SELECT * FROM mention WHERE id = ?", (m["left_mention_id"],)) or {}
        rec = history.current(store, left["source"], left["county"], left["natural_key"]) if left else None
        payload = rec["payload"] if rec else {}
        reason = "declined contact" if outcome == "declined" else "invalid contact"
        if payload.get("personal_rep_name"):
            policy.add_suppression(store, "person", payload["personal_rep_name"], reason, recorded_by)
            effects.append("suppressed person")
        if outcome == "declined" and payload.get("pr_mailing_address"):
            policy.add_suppression(store, "address", payload["pr_mailing_address"], reason, recorded_by)
            effects.append("suppressed address")
        if outcome == "declined":
            policy.add_suppression(store, "parcel", m["right_id"], reason, recorded_by)
            effects.append("suppressed parcel")

    if outcome in NEGATIVE_PARCEL:
        pid = store.scalar("SELECT parcel_id FROM mention WHERE id = ?", (m["right_mention_id"],)) or m["right_id"]
        store.insert(
            "event",
            {
                "kind": NEGATIVE_PARCEL[outcome],
                "source": "outcome",
                "county": m["right_id"].split("/", 1)[0],
                "natural_key": str(match_id),
                "parcel_id": pid,
                "observed_at": now,
                "payload": dumps({"match_id": match_id, "note": note}),
            },
        )
        effects.append("negative parcel signal")
    store.commit()
    return {"id": oid, "match_id": match_id, "lead_id": lead, "outcome": outcome, "effects": effects}


def _match_for_lead(store: Store, lead_id: str) -> Optional[int]:
    for m in store.query("SELECT id, left_id, right_id FROM entity_match"):
        if stable_id(m["left_id"], m["right_id"]) == lead_id:
            return int(m["id"])
    return None


def outcomes_for_match(store: Store, match_id: int) -> List[Dict[str, Any]]:
    return store.query("SELECT * FROM outcome WHERE match_id = ? ORDER BY id", (match_id,))


def latest_outcome(store: Store, match_id: int) -> Optional[str]:
    return store.scalar("SELECT outcome FROM outcome WHERE match_id = ? ORDER BY id DESC LIMIT 1", (match_id,))


def conversion_report(store: Store) -> Dict[str, Any]:
    """For every exported lead: which signals and evidence it carried, and how it ended.

    Signals are read as they stand now, not as they were at export time; good enough to
    rank what is worth weighting, and the export_log timestamp is there for a stricter cut.
    """
    from . import signals as S

    exported = store.query("SELECT DISTINCT match_id FROM export_log WHERE allowed = 1 AND match_id IS NOT NULL")
    by_signal: Dict[str, Dict[str, int]] = defaultdict(lambda: {"leads": 0, "positive": 0, "negative": 0, "open": 0})
    by_evidence: Dict[str, Dict[str, int]] = defaultdict(lambda: {"leads": 0, "positive": 0, "negative": 0, "open": 0})
    totals = {"leads": 0, "positive": 0, "negative": 0, "open": 0}
    for row in exported:
        m = get_match(store, int(row["match_id"]))
        if m is None:
            continue
        last = latest_outcome(store, m["id"])
        bucket = "open" if last is None or last == "no_response" else ("positive" if last in POSITIVE else "negative")
        totals["leads"] += 1
        totals[bucket] += 1
        parcel = entities.get_parcel(store, m["right_id"])
        names = [s["signal"] for s in S.parcel_signals(store, parcel)] if parcel else []
        for name in names:
            by_signal[name]["leads"] += 1
            by_signal[name][bucket] += 1
        for ev in m["evidence"]:
            by_evidence[ev]["leads"] += 1
            by_evidence[ev][bucket] += 1

    def rows(table: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
        out = []
        for name, c in sorted(table.items()):
            decided = c["positive"] + c["negative"]
            out.append(dict(name=name, **c, conversion=round(c["positive"] / decided, 3) if decided else None))
        return out

    return {"totals": totals, "by_signal": rows(by_signal), "by_evidence": rows(by_evidence)}


def format_conversion(report: Dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        "MonitorCLT outreach outcomes -- {0} exported leads: {1} positive, {2} negative, {3} open".format(t["leads"], t["positive"], t["negative"], t["open"]),
        "",
    ]
    for title, key in (("by parcel signal", "by_signal"), ("by match evidence", "by_evidence")):
        lines.append("  " + title)
        for r in report[key]:
            conv = "  n/a" if r["conversion"] is None else "{0:5.1f}%".format(100 * r["conversion"])
            lines.append(
                "    {0:<28} leads {1:>4}  +{2:<3} -{3:<3} open {4:<3} conversion {5}".format(
                    r["name"], r["leads"], r["positive"], r["negative"], r["open"], conv
                )
            )
        lines.append("")
    lines.append("  Conversion is positive / (positive + negative) among decided outcomes. Open leads are not counted against a signal.")
    return "\n".join(lines)
