#!/usr/bin/env python3
"""MonitorCLT <-> Google Calendar sync.

    python3 sync.py calendars                       # list calendar ids (run this first)
    python3 sync.py push --catalysts out/catalysts.jsonl --today 2026-08-26 --dry-run
    python3 sync.py push --catalysts out/catalysts.jsonl --offer-sheet out/offer_sheet.jsonl \
                         --today 2026-08-26
    python3 sync.py pull --out out/calendar_catalysts.jsonl --today 2026-08-26

PUSH is idempotent: every event we own carries a stable key, so a nightly run
patches what moved, adds what's new, and retires future events whose catalyst is
gone. It never touches events it did not create, and never touches the past.

PULL is the other half and the reason this is a bridge rather than a report: dates
you type into Google Calendar yourself come back as EXPLICIT catalyst rows, which
catalyst.py prefers over its own derived offsets. Feed the output straight to the
pipeline:

    python3 ../monitorclt_pipeline/pipeline.py --catalyst-events out/calendar_catalysts.jsonl ...

Config comes from the environment / .env (see config.example.env). Pure stdlib.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import events as events_mod   # noqa: E402
from gcal import Calendar     # noqa: E402
from oauth import AuthError, Credentials  # noqa: E402


def load_dotenv(path=None, env=None):
    """Tiny .env reader (KEY=value, # comments). Existing environment wins, so a
    cron job can override a single value without editing the file."""
    env = env if env is not None else os.environ
    path = path or os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.split("#")[0].strip().strip("'\"")
            if key and key not in env:
                env[key] = value
    return env


def load_jsonl(path):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_rules(path=None):
    return json.load(open(path or os.path.join(HERE, "calendar_rules.json"), encoding="utf-8"))


def _rfc3339(day, end_of_day=False):
    stamp = day.replace(hour=23 if end_of_day else 0, minute=59 if end_of_day else 0,
                        second=59 if end_of_day else 0, tzinfo=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def context_by_apn(offer_sheet_rows):
    """Owner/address/score/offer band from the pipeline, keyed by APN, so each
    event says who and how much -- not just what and when."""
    out = {}
    for row in offer_sheet_rows:
        apn = (row.get("apn") or "").strip()
        if apn:
            out[apn] = row
    return out


# ---- commands ----

def cmd_calendars(args, env):
    creds = Credentials.from_env(env)
    for cal in Calendar(creds).list_calendars():
        primary = "  [primary]" if cal.get("primary") else ""
        access = cal.get("accessRole", "")
        print(f"{cal.get('summary', '?'):<45} {cal.get('id', '')}  ({access}){primary}")
    return 0


def cmd_push(args, env):
    rules = load_rules(args.rules)
    today = datetime.strptime(args.today, "%Y-%m-%d")
    catalysts = load_jsonl(args.catalysts)
    desired = events_mod.desired_events(catalysts, rules,
                                        context_by_apn(load_jsonl(args.offer_sheet)))

    # A dry run is useful before OAuth is set up: with no credentials we cannot
    # diff against the calendar, so we show what would be posted and say so.
    calendar, existing_by_key, offline = None, {}, False
    try:
        creds = Credentials.from_env(env)
        calendar = Calendar(creds, args.calendar_id or env.get("CALENDAR_ID", "primary"))
        horizon = today + timedelta(days=rules.get("push_horizon_days", 120) + 1)
        existing = calendar.list_events(
            time_min=_rfc3339(today), time_max=_rfc3339(horizon, end_of_day=True),
            private_property=f"{events_mod.MARKER}=1")
        for event in existing:
            key = ((event.get("extendedProperties") or {}).get("private") or {}).get("key")
            if key:
                existing_by_key[key] = event
    except AuthError:
        if not args.dry_run:
            raise
        offline = True
        print("(no credentials yet — showing the events that WOULD be posted)\n")

    inserts, patches, deletes = events_mod.reconcile(desired, existing_by_key, today)
    if offline:
        print(f"catalysts in window: {len(desired)}")
    else:
        print(f"catalysts in window: {len(desired)}   "
              f"already on calendar: {len(existing_by_key)}")
    print(f"plan: +{len(inserts)} new   ~{len(patches)} updated   -{len(deletes)} retired")

    if args.dry_run:
        for _key, body in sorted(inserts, key=lambda i: i[1]["start"]["date"])[:20]:
            print(f"  + {body['start']['date']}  {body['summary']}")
        for _key, _id, body in sorted(patches, key=lambda p: p[2]["start"]["date"])[:20]:
            print(f"  ~ {body['start']['date']}  {body['summary']}")
        for _key, _id, summary in deletes[:20]:
            print(f"  - {summary}")
        print("\n(dry run — nothing was written)")
        return 0

    for _key, body in inserts:
        calendar.insert_event(body)
    for _key, event_id, body in patches:
        calendar.patch_event(event_id, body)
    for _key, event_id, _summary in deletes:
        calendar.delete_event(event_id)

    print(f"done: {len(inserts)} created, {len(patches)} updated, {len(deletes)} retired "
          f"on {calendar.calendar_id}")
    return 0


def cmd_pull(args, env):
    rules = load_rules(args.rules)
    today = datetime.strptime(args.today, "%Y-%m-%d")
    window_end = today + timedelta(days=args.horizon_days)

    ids = args.calendar_id or env.get("PULL_CALENDAR_IDS") or env.get("CALENDAR_ID", "primary")
    calendar_ids = [c.strip() for c in ids.split(",") if c.strip()]

    creds = Credentials.from_env(env)
    rows, seen = [], set()
    for calendar_id in calendar_ids:
        found = Calendar(creds, calendar_id).list_events(
            time_min=_rfc3339(today), time_max=_rfc3339(window_end, end_of_day=True))
        parsed = events_mod.to_catalyst_rows(found, rules)
        print(f"{calendar_id}: {len(found)} events -> {len(parsed)} parcel catalysts")
        for row in parsed:
            dedupe = (row["apn"], row["catalyst_type"], row["date"])
            if dedupe not in seen:
                seen.add(dedupe)
                rows.append(row)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        print(f"\nWrote {len(rows)} explicit catalyst rows to {args.out}")
        print("Feed it to the pipeline with --catalyst-events")
    else:
        for row in rows:
            print(json.dumps(row))
    return 0


def main():
    ap = argparse.ArgumentParser(description="MonitorCLT <-> Google Calendar sync")
    ap.add_argument("--env-file", help="path to .env (default: alongside this file)")
    ap.add_argument("--rules", help="calendar_rules.json override")
    sub = ap.add_subparsers(dest="command", required=True)

    p_cal = sub.add_parser("calendars", help="list calendar ids you can reach")
    p_cal.set_defaults(func=cmd_calendars)

    p_push = sub.add_parser("push", help="put upcoming catalysts on the calendar")
    p_push.add_argument("--catalysts", required=True, help="catalysts.jsonl from monitorclt_catalyst")
    p_push.add_argument("--offer-sheet", help="offer_sheet.jsonl from the pipeline (owner/score/band)")
    p_push.add_argument("--calendar-id", help="overrides CALENDAR_ID")
    p_push.add_argument("--today", required=True, help="YYYY-MM-DD (determinism)")
    p_push.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help="read hand-entered dates back as catalysts")
    p_pull.add_argument("--calendar-id", help="comma-separated; overrides PULL_CALENDAR_IDS")
    p_pull.add_argument("--today", required=True, help="YYYY-MM-DD")
    p_pull.add_argument("--horizon-days", type=int, default=365)
    p_pull.add_argument("--out", help="write JSONL here (default: stdout)")
    p_pull.set_defaults(func=cmd_pull)

    args = ap.parse_args()
    env = load_dotenv(args.env_file)
    try:
        return args.func(args, env)
    except AuthError as e:
        print(f"\nNot authorized: {e}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
