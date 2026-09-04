"""Watchlists, notifications, digests and signed webhooks: the product surface.

A watchlist is a filter over the event stream (counties, ZIPs, event kinds, a
polygon or radius, a value floor). Matching events become notifications; a
notification about a confirmed person-link goes through the policy layer first, so
the export boundary is the same whether a row leaves by CSV, webhook or digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import entities, policy
from .events import MATCH_CONFIRMED
from .ids import stable_id
from .normalize.geo import as_point, parse_points, point_in_polygon, within_m
from .normalize.pins import county_norm
from .store import Store, dumps, loads

Sender = Callable[[str, Dict[str, str], bytes], int]  # (url, headers, body) -> HTTP status


def create_watchlist(
    store: Store, name: str, owner: str, filters: Dict[str, Any], channel: str = "digest", endpoint: Optional[str] = None, secret: Optional[str] = None
) -> int:
    wid = store.insert(
        "watchlist",
        {
            "name": name,
            "owner": owner,
            "filters": dumps(filters),
            "channel": channel,
            "endpoint": endpoint,
            "secret": secret,
            "created_at": store.now(),
            "active": 1,
        },
    )
    store.commit()
    return wid


def watchlists(store: Store, active_only: bool = True) -> List[Dict[str, Any]]:
    rows = store.query("SELECT * FROM watchlist" + (" WHERE active = 1" if active_only else "") + " ORDER BY id")
    for r in rows:
        r["filters"] = loads(r["filters"], {})
        r.pop("secret", None)
    return rows


def matches_filter(filters: Dict[str, Any], event: Dict[str, Any], parcel: Optional[Dict[str, Any]]) -> bool:
    if filters.get("kinds") and event["kind"] not in filters["kinds"]:
        return False
    counties = [county_norm(c) for c in filters.get("counties", [])]
    if counties and county_norm(event.get("county")) not in counties:
        return False
    if filters.get("zips"):
        if not parcel or str(parcel.get("zip") or "") not in [str(z) for z in filters["zips"]]:
            return False
    if filters.get("land_use"):
        if not parcel or (parcel.get("land_use") or "") not in filters["land_use"]:
            return False
    if filters.get("min_assessed_value") is not None:
        if not parcel or (parcel.get("assessed_value") or 0) < float(filters["min_assessed_value"]):
            return False
    if filters.get("polygon"):
        pt = as_point(parcel.get("lat"), parcel.get("lon")) if parcel else None
        if not point_in_polygon(pt, parse_points(filters["polygon"])):
            return False
    if filters.get("radius"):
        r = filters["radius"]
        pt = as_point(parcel.get("lat"), parcel.get("lon")) if parcel else None
        if not within_m(pt, (float(r["lat"]), float(r["lon"])), float(r["meters"])):
            return False
    return True


def payload_for(store: Store, event: Dict[str, Any], parcel: Optional[Dict[str, Any]], base_url: str = "monitorclt://") -> Optional[Dict[str, Any]]:
    """What a subscriber receives. Person-level content only via the policy layer."""
    body: Dict[str, Any] = {
        "event_id": stable_id("event", str(event["id"])),
        "kind": event["kind"],
        "county": event.get("county"),
        "occurred_at": event.get("occurred_at"),
        "observed_at": event["observed_at"],
        "parcel": None,
        "detail": {},
    }
    if parcel:
        body["parcel"] = {
            "parcel_id": parcel["id"],
            "pin": parcel["pin"],
            "situs_address": parcel.get("situs_norm"),
            "zip": parcel.get("zip"),
            "land_use": parcel.get("land_use"),
            "assessed_value": parcel.get("assessed_value"),
            "lat": parcel.get("lat"),
            "lon": parcel.get("lon"),
            "permalink": "{0}parcels/{1}".format(base_url, parcel["id"]),
        }
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else loads(event.get("payload"), {})
    if event["kind"] == MATCH_CONFIRMED:
        d = policy.check_export(store, int(payload.get("match_id", 0)), "watchlist", "webhook", base_url)
        if not d.allowed:
            return None
        body["detail"] = d.row or {}
    else:
        # Non-person facts about the parcel: the record's own public fields, no names.
        body["detail"] = {k: v for k, v in payload.items() if not str(k).endswith("_name") and k not in ("from", "to") or k in ("field",)}
        if "field" in payload and payload.get("field") not in ("owner_name", "owner_mailing_address"):
            body["detail"].update({"from": payload.get("from"), "to": payload.get("to")})
    return body


def evaluate_watchlists(store: Store, after_event_id: int = 0, limit: int = 5000, base_url: str = "monitorclt://") -> Dict[str, Any]:
    """Turn new events into notifications for every watchlist whose filter they satisfy."""
    lists = [dict(r, filters=loads(r["filters"], {})) for r in store.query("SELECT * FROM watchlist WHERE active = 1")]
    events = store.query("SELECT * FROM event WHERE id > ? AND kind NOT LIKE 'record_%' ORDER BY id LIMIT ?", (after_event_id, limit))
    created, last_id = 0, after_event_id
    for ev in events:
        last_id = int(ev["id"])
        parcel = entities.get_parcel(store, ev["parcel_id"]) if ev.get("parcel_id") else None
        for wl in lists:
            if not matches_filter(wl["filters"], ev, parcel):
                continue
            if store.one("SELECT 1 FROM notification WHERE watchlist_id = ? AND event_id = ?", (wl["id"], ev["id"])):
                continue
            body = payload_for(store, ev, parcel, base_url)
            if body is None:
                continue
            store.insert(
                "notification", {"watchlist_id": wl["id"], "event_id": ev["id"], "created_at": store.now(), "delivered_at": None, "payload": dumps(body)}
            )
            created += 1
    store.commit()
    return {"events_scanned": len(events), "notifications_created": created, "last_event_id": last_id}


def pending_notifications(store: Store, watchlist_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM notification WHERE delivered_at IS NULL"
    params: List[Any] = []
    if watchlist_id:
        sql += " AND watchlist_id = ?"
        params.append(watchlist_id)
    rows = store.query(sql + " ORDER BY id", params)
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


# --------------------------------------------------------------- webhooks ---


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, body), signature or "")


def webhook_request(watchlist: Dict[str, Any], notifications: List[Dict[str, Any]], now: str) -> Tuple[Dict[str, str], bytes]:
    body = json.dumps({"watchlist": watchlist["name"], "sent_at": now, "notifications": [n["payload"] for n in notifications]}, sort_keys=True).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-MonitorCLT-Event-Count": str(len(notifications))}
    if watchlist.get("secret"):
        headers["X-MonitorCLT-Signature"] = sign(watchlist["secret"], body)
    return headers, body


def deliver(store: Store, sender: Sender, watchlist_id: Optional[int] = None, batch: int = 100) -> List[Dict[str, Any]]:
    out = []
    lists = store.query(
        "SELECT * FROM watchlist WHERE active = 1 AND channel = 'webhook'" + (" AND id = ?" if watchlist_id else ""), ([watchlist_id] if watchlist_id else [])
    )
    for wl in lists:
        notes = pending_notifications(store, wl["id"])[:batch]
        if not notes or not wl.get("endpoint"):
            continue
        headers, body = webhook_request(wl, notes, store.now())
        status = sender(wl["endpoint"], headers, body)
        ok = 200 <= status < 300
        if ok:
            store.executemany("UPDATE notification SET delivered_at = ? WHERE id = ?", [(store.now(), n["id"]) for n in notes])
        store.insert(
            "export_log",
            {
                "at": store.now(),
                "actor": "webhook",
                "channel": "webhook",
                "watchlist_id": wl["id"],
                "match_id": None,
                "allowed": 1 if ok else 0,
                "reasons": dumps([] if ok else ["http_{0}".format(status)]),
                "policy_version": policy.POLICY_VERSION,
            },
        )
        out.append({"watchlist_id": wl["id"], "sent": len(notes), "status": status})
    store.commit()
    return out


# ----------------------------------------------------------------- digest ---


def digest(store: Store, watchlist_id: int, mark_delivered: bool = True) -> str:
    wl = store.one("SELECT * FROM watchlist WHERE id = ?", (watchlist_id,))
    if wl is None:
        raise KeyError("no watchlist {0}".format(watchlist_id))
    notes = pending_notifications(store, watchlist_id)
    lines = ["MonitorCLT digest -- {0} ({1} new)".format(wl["name"], len(notes)), ""]
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for n in notes:
        by_kind.setdefault(n["payload"]["kind"], []).append(n)
    for kind in sorted(by_kind):
        lines.append(kind.replace("_", " ").upper())
        for n in by_kind[kind]:
            p = n["payload"]
            parcel = p.get("parcel") or {}
            where = parcel.get("situs_address") or p.get("county") or ""
            extra = ""
            if kind == MATCH_CONFIRMED:
                d = p["detail"]
                extra = "  contact: {0} ({1})".format(d.get("contact_name"), d.get("contact_role"))
            elif p.get("detail", {}).get("field"):
                extra = "  {0}: {1} -> {2}".format(p["detail"]["field"], p["detail"].get("from"), p["detail"].get("to"))
            lines.append("  {0:<10} {1:<40} {2}{3}".format(p.get("occurred_at") or p["observed_at"][:10], where[:40], parcel.get("pin") or "", extra))
        lines.append("")
    if mark_delivered and notes:
        store.executemany("UPDATE notification SET delivered_at = ? WHERE id = ?", [(store.now(), n["id"]) for n in notes])
        store.insert(
            "export_log",
            {
                "at": store.now(),
                "actor": wl["owner"],
                "channel": "digest",
                "watchlist_id": watchlist_id,
                "match_id": None,
                "allowed": 1,
                "reasons": "[]",
                "policy_version": policy.POLICY_VERSION,
            },
        )
        store.commit()
    return "\n".join(lines)
