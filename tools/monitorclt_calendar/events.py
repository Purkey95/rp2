"""Pure mapping between MonitorCLT catalysts and Google Calendar events.

Both directions live here, with no network and no clock of their own, so the
whole bridge is testable offline (see test_calendar.py):

  PUSH   catalyst rows (tools/monitorclt_catalyst output) -> Google event bodies.
         Every event carries extendedProperties.private.key -- a stable hash of
         (apn, catalyst_type, date) -- so re-running reconciles in place instead
         of stacking duplicates on the calendar.

  PULL   Google events -> EXPLICIT catalyst rows for catalyst.py. A date you type
         into the calendar yourself ("Rezoning hearing APN 12345678") comes back
         as a real dated catalyst; catalyst.py already prefers explicit dates over
         derived offsets, so a hand-entered hearing date beats a guessed one.

Pure stdlib.
"""

import hashlib
import re
from datetime import datetime, timedelta

MARKER = "monitorclt"  # extendedProperties.private flag identifying events we own

# Fields we manage on an event. Anything else the human edits (attendees, notes
# appended below our block, colour overrides they set later) is left alone.
MANAGED_FIELDS = ("summary", "description", "start", "end")


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def sync_key(apn, catalyst_type, date):
    """Stable per-catalyst identity. Same catalyst on a later run -> same key ->
    the existing event is patched. A moved date is a NEW key: the old event is
    retired and the new date is added, which is what you want on a calendar."""
    raw = f"{apn}|{catalyst_type}|{date[:10]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]  # nosec B324 - identity, not security


def _color_for(days_until, rules):
    colors = rules.get("colors", {})
    for cutoff in sorted((int(k) for k in colors if k.lstrip("-").isdigit())):
        if days_until <= cutoff:
            return colors[str(cutoff)]
    return colors.get("default")


def _title(catalyst, context, rules):
    label = catalyst["catalyst_type"].replace("_", " ")
    address = (context.get("situs_address") or "").strip()
    if not address:
        address = rules.get("title_fallback_address", "APN {apn}").format(apn=catalyst["apn"])
    return rules.get("title_template", "{catalyst} — {address}").format(
        catalyst=label, address=address)


def _description(catalyst, context, rules):
    """Human-readable body. The `APN:` line is also what lets an event we did not
    create -- or one whose extended properties a calendar client dropped -- still
    round-trip back to a parcel on pull."""
    lines = [f"APN: {catalyst['apn']}"]
    if context.get("owner"):
        lines.append(f"Owner: {context['owner']}")
    if context.get("seller_opportunity_score") is not None:
        band = context.get("band")
        lines.append(f"Seller Opportunity Score: {context['seller_opportunity_score']}"
                     + (f" ({band})" if band else ""))
    val = context.get("valuation") or {}
    band_v = val.get("offer_band") if isinstance(val, dict) else None
    if band_v:
        high = band_v.get("market_adjusted_high", band_v.get("as_is_high"))
        if high is not None:
            lines.append(f"Offer band high: {high}")
    if catalyst.get("derived"):
        note = catalyst.get("note") or "projected from a dated signal, not a recorded date"
        lines.append(f"DERIVED (approximate): {note}")
    else:
        lines.append("Explicit recorded date.")
    if catalyst.get("source_url"):
        lines.append(f"Source: {catalyst['source_url']}")
    lines.append("")
    lines.append("— posted by MonitorCLT; edits to the title/date are not preserved "
                 "across syncs, but adding your own events here is (they are read "
                 "back as catalysts).")
    return "\n".join(lines)


def event_body(catalyst, context, rules):
    """One catalyst -> one all-day Google Calendar event body."""
    start = catalyst["date"][:10]
    end = (_date(start) + timedelta(days=1)).strftime("%Y-%m-%d")  # end date is exclusive
    key = sync_key(catalyst["apn"], catalyst["catalyst_type"], start)

    body = {
        "summary": _title(catalyst, context, rules),
        "description": _description(catalyst, context, rules),
        "start": {"date": start},
        "end": {"date": end},
        "transparency": "transparent",  # a catalyst does not make you busy
        "extendedProperties": {"private": {
            MARKER: "1",
            "key": key,
            "apn": str(catalyst["apn"])[:1024],
            "catalyst_type": catalyst["catalyst_type"][:1024],
        }},
    }

    color = _color_for(catalyst.get("days_until", 999), rules)
    if color:
        body["colorId"] = str(color)

    reminders = rules.get("reminder_days_before") or []
    if reminders:
        body["reminders"] = {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": int(d) * 24 * 60} for d in reminders]}

    src = catalyst.get("source_url") or ""
    if src.startswith("http"):  # Google rejects a source url that is not a real URL
        body["source"] = {"title": "MonitorCLT source record", "url": src[:1024]}

    return key, body


def desired_events(catalyst_results, rules, context_by_apn=None):
    """Flatten catalyst.score_parcels() output into {key: body} for every catalyst
    inside push_horizon_days. Parcels the pipeline gave us context for (owner,
    address, score, offer band) get a richer event."""
    context_by_apn = context_by_apn or {}
    horizon = rules.get("push_horizon_days", 120)
    out = {}
    for parcel in catalyst_results:
        for catalyst in parcel.get("catalysts", []):
            if catalyst.get("days_until", 0) > horizon or catalyst.get("days_until", 0) < 0:
                continue
            key, body = event_body(catalyst, context_by_apn.get(catalyst["apn"], {}), rules)
            out[key] = body
    return out


def _managed_diff(desired_body, existing_event):
    """True when a field we own has drifted (or the event was edited away)."""
    for field in MANAGED_FIELDS:
        if desired_body.get(field) != existing_event.get(field):
            return True
    want_color = desired_body.get("colorId")
    return bool(want_color) and existing_event.get("colorId") != want_color


def reconcile(desired, existing_by_key, today):
    """Idempotent three-way plan against the events we already own.

    inserts  desired keys with no event yet
    patches  desired keys whose managed fields drifted
    deletes  events we own that are no longer desired AND still in the future --
             past events are history and are never touched.
    """
    inserts, patches, deletes = [], [], []
    for key, body in sorted(desired.items()):
        found = existing_by_key.get(key)
        if not found:
            inserts.append((key, body))
        elif _managed_diff(body, found):
            patches.append((key, found["id"], body))

    for key, event in sorted(existing_by_key.items()):
        if key in desired:
            continue
        start = (event.get("start") or {}).get("date") or (event.get("start") or {}).get("dateTime")
        if start and _date(start) >= today:
            deletes.append((key, event["id"], event.get("summary", "")))
    return inserts, patches, deletes


# ---- pull: calendar -> catalyst rows ----

def parse_apn(text, rules):
    for pattern in rules.get("apn_patterns", []):
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def classify(text, rules):
    """Longest alias wins, so 'ground lease' is not swallowed by 'lease expires'."""
    low = (text or "").lower()
    best = None
    for alias, catalyst_type in rules.get("inbound_aliases", {}).items():
        if alias in low and (best is None or len(alias) > len(best[0])):
            best = (alias, catalyst_type)
    return best[1] if best else None


def event_to_catalyst_row(event, rules):
    """A calendar event -> an explicit catalyst row, or None if it is not about a
    parcel. Events MonitorCLT itself posted are skipped: pushing them back in
    would launder a derived guess into an explicit date."""
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    if private.get(MARKER) == "1":
        return None

    text = " ".join(filter(None, [event.get("summary", ""), event.get("description", ""),
                                  event.get("location", "")]))
    apn = private.get("apn") or parse_apn(text, rules)
    if not apn:
        return None
    catalyst_type = private.get("catalyst_type") or classify(text, rules)
    if not catalyst_type:
        return None

    start = (event.get("start") or {})
    date = start.get("date") or (start.get("dateTime") or "")[:10]
    if not date:
        return None

    return {
        "apn": apn.strip(),
        "catalyst_type": catalyst_type,
        "date": date[:10],
        "source_url": event.get("htmlLink", "") or "google-calendar",
        "source_name": "Google Calendar (hand-entered)",
    }


def to_catalyst_rows(events, rules):
    rows = []
    for event in events:
        if (event.get("status") or "").lower() == "cancelled":
            continue
        row = event_to_catalyst_row(event, rules)
        if row:
            rows.append(row)
    return rows
