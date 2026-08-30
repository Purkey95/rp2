#!/usr/bin/env python3
"""Catalyst Calendar — "what is about to happen to this parcel?"

The static score says how distressed a parcel is *now*; the timeline says why
*now*. This is the third temporal axis: what's coming *next*. Almost every record
we ingest has a date attached, and many of those dates point at a FUTURE milestone
that will move the property -- a private loan maturing, a rezoning hearing, tax-
foreclosure eligibility, an entitlement or ground lease expiring, a special
assessment taking effect. This projects those dates forward per parcel so we can
ask the operationally useful question:

    "Which 37 parcels have a meaningful catalyst in the next 60 days?"

Two ways a catalyst enters (see catalyst_rules.json):
  EXPLICIT  a record already carries a real future date  -> pass through.
  DERIVED   a present-dated signal implies a milestone at a known offset
            (tax_delinquency today -> foreclosure eligibility ~2yr out).

Each upcoming catalyst is weighted by its type and by IMMINENCE (closer = higher).
Per parcel we emit the sorted upcoming catalysts, days_until each, and a
catalyst_horizon score (0-100). It pairs with the lifecycle engine: decay retires
stale leads, the calendar surfaces ripening ones -- same dated-event machinery,
both directions. Pass --today for determinism. Pure stdlib.
"""

import argparse
import json
import os
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def imminence(days_until, rules):
    """Closer catalysts weigh more. days_until <= bucket cutoff -> that weight."""
    if days_until < 0:
        return 0.0
    for cutoff, w in rules["horizon_buckets_days"]:
        if days_until <= cutoff:
            return w
    return rules["horizon_floor"]


def project_catalysts(rows, rules, today):
    """Turn raw rows into dated future catalysts. A row is EXPLICIT if it names a
    catalyst_type; otherwise we try DERIVED rules against its signal_type.

    Returns list of {apn, catalyst_type, date, days_until, source_url, derived, note}.
    Only catalysts dated today-or-later are returned (past ones belong to the
    timeline/lifecycle engines, not the calendar)."""
    derived_by_signal = {}
    for r in rules["derived_catalysts"]:
        derived_by_signal.setdefault(r["from"], []).append(r)

    out = []
    horizon_cap = today + timedelta(days=rules.get("default_horizon_days", 365))
    for row in rows:
        apn = (row.get("apn") or "").strip()
        if not apn or not row.get("date"):
            continue
        base = _date(row["date"])

        candidates = []
        if row.get("catalyst_type"):
            candidates.append((row["catalyst_type"], base, False, ""))
        else:
            for rule in derived_by_signal.get(row.get("signal_type", ""), []):
                candidates.append((rule["catalyst"], base + timedelta(days=rule["offset_days"]),
                                   True, rule.get("note", "")))

        for ctype, cdate, derived, note in candidates:
            days_until = (cdate - today).days
            if days_until < 0 or cdate > horizon_cap:
                continue
            out.append({
                "apn": apn, "catalyst_type": ctype, "date": cdate.strftime("%Y-%m-%d"),
                "days_until": days_until, "source_url": row.get("source_url", ""),
                "derived": derived, "note": note,
            })
    return out


def score_parcels(rows, rules, today):
    catalysts = project_catalysts(rows, rules, today)
    weights = rules["catalyst_weights"]

    by_apn = {}
    for c in catalysts:
        by_apn.setdefault(c["apn"], []).append(c)

    results = []
    for apn, cs in by_apn.items():
        cs.sort(key=lambda c: c["days_until"])
        horizon = 0.0
        for c in cs:
            w = weights.get(c["catalyst_type"], 8)
            horizon += w * imminence(c["days_until"], rules)
        results.append({
            "apn": apn,
            "catalyst_horizon": round(min(horizon, 100), 1),
            "next_catalyst": cs[0]["catalyst_type"],
            "next_in_days": cs[0]["days_until"],
            "catalysts": cs,
        })
    results.sort(key=lambda r: (r["next_in_days"], -r["catalyst_horizon"]))
    return results


def upcoming_within(results, days):
    """The headline query: parcels with any catalyst inside the window."""
    return [r for r in results if r["next_in_days"] <= days]


def to_signals(results):
    """Emit a catalyst_horizon signal per parcel so the score consumes it like any
    other (dimension: disposition_probability). source_url documents the trigger."""
    out = []
    for r in results:
        c = r["catalysts"][0]
        out.append({
            "apn": r["apn"], "signal_type": "catalyst_horizon", "bucket": "active",
            "source_name": "MonitorCLT catalyst calendar",
            "source_url": f"next: {c['catalyst_type']} in {c['days_until']}d ({c['date']})"
                          + ("  [derived]" if c["derived"] else ""),
            "catalyst_horizon": r["catalyst_horizon"],
        })
    return out


def load_rows(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="Catalyst Calendar (what's about to happen)")
    ap.add_argument("--events", required=True, help="dated rows JSONL (explicit catalysts and/or signals)")
    ap.add_argument("--rules", default=os.path.join(HERE, "catalyst_rules.json"))
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (determinism)")
    ap.add_argument("--within", type=int, default=60, help="headline horizon window (days)")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    today = _date(args.today)
    results = score_parcels(load_rows(args.events), rules, today)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "catalysts.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(args.outdir, "catalyst_signals.jsonl"), "w", encoding="utf-8") as f:
        for s in to_signals(results):
            f.write(json.dumps(s) + "\n")

    window = upcoming_within(results, args.within)
    print(f"Parcels with a catalyst: {len(results)}")
    print(f">>> {len(window)} parcels have a catalyst within {args.within} days\n")
    for r in window[:12]:
        tag = " [derived]" if r["catalysts"][0]["derived"] else ""
        print(f"  APN {r['apn']:<14} in {r['next_in_days']:>4}d  {r['next_catalyst']:<28} "
              f"horizon {r['catalyst_horizon']}{tag}")
    print(f"\nWrote {args.outdir}/catalysts.jsonl, catalyst_signals.jsonl")


if __name__ == "__main__":
    main()
