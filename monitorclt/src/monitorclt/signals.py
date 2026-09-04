"""The parcel signal stack: every active, documented signal on a property, and a
transparent composite rank.

This is where multi-source pays off. A parcel with an open estate, a delinquent tax
bill and an out-of-state mailing address is a different situation from any one of
those alone. Each signal is a fact from a public record with a fixed, documented
weight; the score is their sum; the explanation is the list. No signal is or proxies
a protected characteristic, and there is no criminal-justice signal because there is
no such data in the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import history
from .clock import parse_ts
from .normalize.addresses import parse_address
from .store import Store, loads

HOME_STATE = "NC"


@dataclass
class Signal:
    name: str
    weight: float
    description: str


SIGNALS: Dict[str, Signal] = {
    "estate_confirmed": Signal("estate_confirmed", 40, "A confirmed probate link: the estate appears to hold this parcel."),
    "estate_pending_review": Signal("estate_pending_review", 10, "A probate candidate awaiting human review."),
    "estate_marker_on_owner": Signal("estate_marker_on_owner", 25, "Owner string reads ESTATE OF / HEIRS with no linked case (input gap)."),
    "foreclosure_pending": Signal("foreclosure_pending", 35, "A substitute-trustee foreclosure is pending on this parcel."),
    "tax_delinquent": Signal("tax_delinquent", 20, "On the delinquent tax list; +5 per additional year."),
    "code_case_open": Signal("code_case_open", 15, "An open code-enforcement case on the property."),
    "absentee_owner": Signal("absentee_owner", 10, "Tax mailing address differs from the situs address."),
    "out_of_state_owner": Signal("out_of_state_owner", 10, "Tax mailing address is outside {0}.".format(HOME_STATE)),
    "long_tenure": Signal("long_tenure", 8, "Last recorded sale more than 15 years ago."),
    "post_death_conveyance": Signal(
        "post_death_conveyance", -20, "A deed from the decedent was recorded after the date of death; the parcel may have left the estate."
    ),
    "recent_owner_change": Signal("recent_owner_change", -15, "Owner string changed within the last 180 days."),
    "entity_owner": Signal("entity_owner", 0, "Owner is an organization (informational)."),
}


def _days_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    try:
        return (parse_ts(str(b)[:10]) - parse_ts(str(a)[:10])).days
    except ValueError:
        return None


def parcel_signals(store: Store, parcel: Dict[str, Any]) -> List[Dict[str, Any]]:
    pid = parcel["id"]
    now = store.now()
    found: List[Dict[str, Any]] = []

    def add(name: str, detail: Dict[str, Any], weight: Optional[float] = None) -> None:
        sig = SIGNALS[name]
        found.append({"signal": name, "weight": sig.weight if weight is None else weight, "description": sig.description, "detail": detail})

    matches = store.query("SELECT id, status, probability, flags, left_id FROM entity_match WHERE right_source = 'parcel' AND right_id = ?", (pid,))
    for m in matches:
        if m["status"] == "confirmed":
            add("estate_confirmed", {"match_id": m["id"], "estate": m["left_id"], "probability": m["probability"]})
            if "post_death_conveyance" in loads(m["flags"], []):
                add("post_death_conveyance", {"match_id": m["id"]})
        elif m["status"] == "pending":
            add("estate_pending_review", {"match_id": m["id"], "estate": m["left_id"], "probability": m["probability"]})
    if not matches:
        markers = [
            loads(r["parsed"], {}).get("markers", [])
            for r in store.query("SELECT parsed FROM mention WHERE source = 'parcel' AND role = 'owner' AND current = 1 AND parcel_id = ?", (pid,))
        ]
        markers = sorted({m for ms in markers for m in ms})
        if markers:
            add("estate_marker_on_owner", {"markers": markers})

    for rec in _current_for_parcel(store, "foreclosure", pid):
        if str(rec.get("status") or "").upper() not in ("DISMISSED", "WITHDRAWN", "SOLD", "CLOSED"):
            add(
                "foreclosure_pending",
                {
                    "file_number": rec.get("file_number"),
                    "filing_date": rec.get("filing_date"),
                    "hearing_date": rec.get("hearing_date"),
                    "status": rec.get("status"),
                },
            )
    tax = _current_for_parcel(store, "tax_delinquency", pid)
    if tax:
        years = max(int(r.get("years_delinquent") or 1) for r in tax)
        add(
            "tax_delinquent",
            {
                "tax_years": sorted(str(r.get("tax_year")) for r in tax),
                "amount_due": round(sum(float(r.get("amount_due") or 0) for r in tax), 2),
                "years_delinquent": years,
            },
            SIGNALS["tax_delinquent"].weight + 5 * max(0, years - 1),
        )
    for rec in _current_for_parcel(store, "code_enforcement", pid):
        if str(rec.get("status") or "").upper() not in ("CLOSED", "COMPLIED", "RESOLVED"):
            add("code_case_open", {"case_number": rec.get("case_number"), "violation_type": rec.get("violation_type"), "opened_date": rec.get("opened_date")})

    mailing = parse_address(parcel.get("owner_mailing_norm"))
    situs = parse_address(parcel.get("situs_norm"))
    if not mailing.is_empty and not situs.is_empty and mailing.street_key != situs.street_key:
        add("absentee_owner", {"mailing": parcel.get("owner_mailing_norm"), "situs": parcel.get("situs_norm")})
    if mailing.state and mailing.state != HOME_STATE:
        add("out_of_state_owner", {"state": mailing.state})
    tenure = _days_between(parcel.get("last_sale_date"), now)
    if tenure is not None and tenure > 15 * 365:
        add("long_tenure", {"last_sale_date": parcel.get("last_sale_date"), "years": round(tenure / 365, 1)})
    recent = store.one("SELECT observed_at FROM event WHERE parcel_id = ? AND kind = 'owner_changed' ORDER BY id DESC LIMIT 1", (pid,))
    if recent and (_days_between(recent["observed_at"], now) or 0) <= 180:
        add("recent_owner_change", {"observed_at": recent["observed_at"]})
    if store.one("SELECT 1 FROM mention WHERE source = 'parcel' AND role = 'owner' AND current = 1 AND parcel_id = ? AND is_organization = 1", (pid,)):
        add("entity_owner", {})
    return found


def _current_for_parcel(store: Store, source: str, pid: str) -> List[Dict[str, Any]]:
    rows = store.query(
        "SELECT DISTINCT r.payload FROM mention m JOIN record_version r ON r.id = m.record_version_id "
        "WHERE m.source = ? AND m.current = 1 AND m.parcel_id = ? AND r.superseded_at IS NULL AND r.retired = 0",
        (source, pid),
    )
    if rows:
        return [loads(r["payload"], {}) for r in rows]
    # records with no person mention still count (an organization owner, an empty name)
    out = []
    for rec in history.current_records(store, source):
        from .normalize.pins import parcel_id

        pin = rec["payload"].get("pin") or rec["payload"].get("parcel_pin")
        if pin and parcel_id(rec["payload"].get("county") or rec["county"], pin) == pid:
            out.append(rec["payload"])
    return out


def score(signals: List[Dict[str, Any]]) -> float:
    return round(sum(float(s["weight"]) for s in signals), 1)


def rank(store: Store, county: Optional[str] = None, limit: int = 50, min_score: float = 1.0, zips: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM parcel WHERE retired_at IS NULL"
    params: List[Any] = []
    if county:
        sql += " AND county = ?"
        params.append(county.upper())
    if zips:
        sql += " AND zip IN ({0})".format(", ".join("?" for _ in zips))
        params.extend(zips)
    out = []
    for parcel in store.query(sql, params):
        sigs = parcel_signals(store, parcel)
        total = score(sigs)
        if total < min_score:
            continue
        out.append(
            {
                "parcel_id": parcel["id"],
                "pin": parcel["pin"],
                "situs_address": parcel.get("situs_norm"),
                "zip": parcel.get("zip"),
                "assessed_value": parcel.get("assessed_value"),
                "score": total,
                "signals": sigs,
            }
        )
    out.sort(key=lambda r: (-r["score"], r["parcel_id"]))
    return out[:limit]


def format_rank(rows: List[Dict[str, Any]]) -> str:
    lines = ["MonitorCLT parcel signal stack ({0} parcels)".format(len(rows)), ""]
    for r in rows:
        lines.append("{0:>6.1f}  {1:<24} {2}".format(r["score"], r["parcel_id"], r.get("situs_address") or ""))
        for s in r["signals"]:
            lines.append("          {0:+5.0f}  {1:<24} {2}".format(s["weight"], s["signal"], s["description"]))
        lines.append("")
    lines.append("Scores rank public-record facts about the property; they say nothing about any person.")
    return "\n".join(lines)
