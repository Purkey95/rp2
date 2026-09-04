"""The policy layer: deny by default, and every exit logged.

Nothing reaches a CSV, webhook, digest or API response about a *person* unless it
passes check_export. The rules are executable versions of the README's ethics:
confirmed links only (a pending candidate is a question, not a lead), corroborated
evidence, no suppressed person/address/parcel, inside retention, and the contact
that leaves is the personal representative or estate attorney -- never a list of
decedents. There is no criminal-justice input anywhere, so there is nothing here to
gate about it; the gate is that the tables do not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import entities, history
from .clock import parse_ts
from .ids import stable_id
from .normalize.addresses import parse_address
from .normalize.names import clean_text
from .resolve.resolver import get_match
from .store import Store, dumps, loads

POLICY_VERSION = "2.0"

# The only person-level fields an export may carry. Everything else is redacted.
EXPORT_FIELDS = (
    "lead_id",
    "county",
    "file_number",
    "decedent_name",
    "filing_date",
    "case_status",
    "contact_role",
    "contact_name",
    "contact_mailing_address",
    "parcel_id",
    "pin",
    "situs_address",
    "land_use",
    "assessed_value",
    "match_probability",
    "evidence",
    "flags",
    "confirmed_by",
    "permalink",
)


@dataclass
class Decision:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    row: Optional[Dict[str, Any]] = None


# ----------------------------------------------------------- suppression ----


def add_suppression(store: Store, kind: str, value: str, reason: str, actor: str = "system", expires_at: Optional[str] = None) -> int:
    norm = _norm_value(kind, value)
    store.execute(
        "INSERT INTO suppression (kind, value_norm, reason, added_by, added_at, expires_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(kind, value_norm) DO UPDATE SET reason = excluded.reason, added_by = excluded.added_by, added_at = excluded.added_at, expires_at = excluded.expires_at",
        (kind, norm, reason, actor, store.now(), expires_at),
    )
    store.insert(
        "access_log",
        {"at": store.now(), "actor": actor, "action": "suppression.add", "target": "{0}/{1}".format(kind, norm), "detail": dumps({"reason": reason})},
    )
    store.commit()
    return int(store.scalar("SELECT id FROM suppression WHERE kind = ? AND value_norm = ?", (kind, norm)))


def _norm_value(kind: str, value: str) -> str:
    if kind == "address":
        return parse_address(value).normalized
    if kind == "person":
        from .normalize.names import parse_name

        return parse_name(value, "first_last").normalized
    return clean_text(value)


def is_suppressed(store: Store, kind: str, value_norm: Optional[str]) -> Optional[str]:
    if not value_norm:
        return None
    row = store.one("SELECT reason, expires_at FROM suppression WHERE kind = ? AND value_norm = ?", (kind, value_norm))
    if row is None:
        return None
    if row["expires_at"] and row["expires_at"] <= store.now():
        return None
    return str(row["reason"])


# -------------------------------------------------------------- retention ---


def set_retention(store: Store, scope: str, ttl_days: int, note: Optional[str] = None) -> None:
    store.execute("INSERT OR REPLACE INTO retention_policy (scope, ttl_days, note) VALUES (?, ?, ?)", (scope, ttl_days, note))
    store.commit()


def default_retention(store: Store) -> None:
    from .sources.base import registry

    for county in registry.counties():
        for c in registry.connectors(county):
            if store.one("SELECT 1 FROM retention_policy WHERE scope = ?", (c.name,)) is None:
                set_retention(store, c.name, c.spec.retention_days, c.spec.description)
    for scope, days in (("raw", 365), ("event", 730)):
        if store.one("SELECT 1 FROM retention_policy WHERE scope = ?", (scope,)) is None:
            set_retention(store, scope, days)


def _cutoff(store: Store, ttl_days: int) -> str:
    import datetime as dt

    from .clock import fmt_ts

    return fmt_ts(parse_ts(store.now()) - dt.timedelta(days=ttl_days))


def purge_expired(store: Store, actor: str = "retention-job") -> Dict[str, int]:
    """Delete what retention says we should not still hold. Audit logs are never purged.

    Superseded and retired versions (and their mentions) past TTL go; a *current* live
    record is the public record as it stands and is kept. Raw bodies go once nothing
    references them and they are past the raw TTL. Events go past the event TTL.
    """
    counts = {"record_versions": 0, "mentions": 0, "raw_captures": 0, "events": 0}
    for pol in store.query("SELECT scope, ttl_days FROM retention_policy"):
        cutoff = _cutoff(store, int(pol["ttl_days"]))
        if pol["scope"] == "raw":
            cur = store.execute(
                "DELETE FROM raw_capture WHERE fetched_at < ? AND id NOT IN (SELECT raw_capture_id FROM record_version WHERE raw_capture_id IS NOT NULL)",
                (cutoff,),
            )
            counts["raw_captures"] += cur.rowcount
        elif pol["scope"] == "event":
            cur = store.execute("DELETE FROM event WHERE observed_at < ? AND id NOT IN (SELECT event_id FROM notification)", (cutoff,))
            counts["events"] += cur.rowcount
        else:
            ids = [
                r["id"]
                for r in store.query(
                    "SELECT id FROM record_version WHERE source = ? AND ((superseded_at IS NOT NULL AND superseded_at < ?) OR (retired = 1 AND observed_at < ?))",
                    (pol["scope"], cutoff, cutoff),
                )
            ]
            for vid in ids:
                counts["mentions"] += store.execute("DELETE FROM mention WHERE record_version_id = ?", (vid,)).rowcount
                store.execute("UPDATE event SET record_version_id = NULL WHERE record_version_id = ?", (vid,))
                counts["record_versions"] += store.execute("DELETE FROM record_version WHERE id = ?", (vid,)).rowcount
    store.insert("access_log", {"at": store.now(), "actor": actor, "action": "retention.purge", "target": None, "detail": dumps(counts)})
    store.commit()
    return counts


# ----------------------------------------------------------------- export ---


def check_export(store: Store, match_id: int, actor: str, channel: str, base_url: str = "monitorclt://", log: bool = True) -> Decision:
    m = get_match(store, match_id)
    reasons: List[str] = []
    if m is None:
        return _log(store, Decision(False, ["no_such_match"]), actor, channel, match_id, None, log)
    if m["status"] != "confirmed":
        reasons.append("not_confirmed:" + m["status"])
    if m["decided_by"] != "reviewer" and not m["gates"].get("corroboration_required", False):
        reasons.append("uncorroborated")
    if m["gates"] and not all(m["gates"].values()) and m["decided_by"] != "reviewer":
        reasons.append("gate_failed")
    left = store.one("SELECT * FROM mention WHERE id = ?", (m["left_mention_id"],)) or {}
    left_rec = history.current(store, left["source"], left["county"], left["natural_key"]) if left else None
    payload = left_rec["payload"] if left_rec else {}
    parcel = entities.get_parcel(store, m["right_id"]) or {}

    contact_name = payload.get("personal_rep_name") or payload.get("estate_attorney_name")
    contact_role = "personal_representative" if payload.get("personal_rep_name") else ("estate_attorney" if payload.get("estate_attorney_name") else None)
    contact_addr = payload.get("pr_mailing_address")
    if not contact_name:
        reasons.append("no_authorized_contact")  # the decedent is never the contact

    from .normalize.names import parse_name

    checks = [
        ("person", parse_name(contact_name, "first_last").normalized if contact_name else None),
        ("person", entities.mention_name(left).normalized if left else None),
        ("address", parse_address(contact_addr).normalized if contact_addr else None),
        ("parcel", clean_text(m["right_id"])),
        ("county", clean_text(m["right_id"].split("/", 1)[0])),
    ]
    for kind, value in checks:
        why = is_suppressed(store, kind, value)
        if why:
            reasons.append("suppressed:{0}:{1}".format(kind, why))

    pol = store.one("SELECT ttl_days FROM retention_policy WHERE scope = ?", (left.get("source"),)) if left else None
    if pol and left_rec and left_rec["observed_at"] < _cutoff(store, int(pol["ttl_days"])):
        reasons.append("retention_expired")

    row = None
    if not reasons:
        row = {
            "lead_id": stable_id(m["left_id"], m["right_id"]),
            "county": m["right_id"].split("/", 1)[0],
            "file_number": payload.get("file_number"),
            "decedent_name": payload.get("decedent_name"),
            "filing_date": payload.get("filing_date"),
            "case_status": payload.get("case_status"),
            "contact_role": contact_role,
            "contact_name": contact_name,
            "contact_mailing_address": contact_addr,
            "parcel_id": m["right_id"],
            "pin": parcel.get("pin"),
            "situs_address": parcel.get("situs_norm"),
            "land_use": parcel.get("land_use"),
            "assessed_value": parcel.get("assessed_value"),
            "match_probability": m["probability"],
            "evidence": m["evidence"],
            "flags": m["flags"],
            "confirmed_by": m["decided_by"],
            "permalink": "{0}matches/{1}".format(base_url, m["id"]),
        }
        row = {k: row.get(k) for k in EXPORT_FIELDS}
    return _log(store, Decision(not reasons, reasons, row), actor, channel, match_id, None, log)


def _log(store: Store, d: Decision, actor: str, channel: str, match_id: Optional[int], watchlist_id: Optional[int], log: bool) -> Decision:
    if log:
        store.insert(
            "export_log",
            {
                "at": store.now(),
                "actor": actor,
                "channel": channel,
                "watchlist_id": watchlist_id,
                "match_id": match_id,
                "allowed": 1 if d.allowed else 0,
                "reasons": dumps(d.reasons),
                "policy_version": POLICY_VERSION,
            },
        )
    return d


def export(store: Store, actor: str, channel: str = "csv", match_ids: Optional[List[int]] = None, base_url: str = "monitorclt://") -> Dict[str, Any]:
    """Run every candidate through the policy; return the rows that passed and why the rest did not."""
    if match_ids is None:
        match_ids = [r["id"] for r in store.query("SELECT id FROM entity_match WHERE status = 'confirmed' ORDER BY id")]
    rows, denied = [], []
    for mid in match_ids:
        d = check_export(store, mid, actor, channel, base_url)
        if d.allowed and d.row:
            rows.append(d.row)
        else:
            denied.append({"match_id": mid, "reasons": d.reasons})
    store.insert(
        "access_log",
        {"at": store.now(), "actor": actor, "action": "export." + channel, "target": None, "detail": dumps({"allowed": len(rows), "denied": len(denied)})},
    )
    store.commit()
    return {"rows": rows, "denied": denied, "policy_version": POLICY_VERSION}


def why_do_you_have_this(store: Store, lead_id: str) -> Optional[Dict[str, Any]]:
    """The provenance trail for one exported lead: where every field came from and who confirmed it."""
    for m in store.query("SELECT id, left_id, right_id FROM entity_match WHERE status = 'confirmed'"):
        if stable_id(m["left_id"], m["right_id"]) == lead_id:
            match = get_match(store, m["id"]) or {}
            left = store.one("SELECT * FROM mention WHERE id = ?", (match["left_mention_id"],)) or {}
            rec = history.current(store, left["source"], left["county"], left["natural_key"]) if left else None
            raw = (
                store.one("SELECT id, url, fetched_at, content_hash FROM raw_capture WHERE id = ?", (rec["raw_capture_id"],))
                if rec and rec.get("raw_capture_id")
                else None
            )
            exports = store.query("SELECT at, actor, channel, allowed FROM export_log WHERE match_id = ? ORDER BY id", (m["id"],))
            return {
                "lead_id": lead_id,
                "match": match,
                "source_record": rec,
                "raw_capture": raw,
                "exports": exports,
                "decisions": store.query("SELECT * FROM review_decision WHERE match_id = ? ORDER BY id", (m["id"],)),
            }
    return None


def access_log(store: Store, limit: int = 100) -> List[Dict[str, Any]]:
    rows = store.query("SELECT * FROM access_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["detail"] = loads(r["detail"], {})
    return rows
