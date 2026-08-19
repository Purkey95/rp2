#!/usr/bin/env python3
"""Opportunity-Decay — the lead lifecycle engine.

Every other part of MonitorCLT *detects* opportunity. This is the only part that
*retires* it. Without it, a parcel that scored 80 because of a tax delinquency
that's since been paid — or one that sold last month — sits at 80 forever, and the
lead list slowly fills with ghosts. A lead is not a fact; it's a lifecycle: it
strengthens, weakens, resolves, or expires.

Three mechanisms (see lifecycle_rules.json):

  RESOLUTION  a resolving event AFTER the signal cancels it — tax paid, lien
              released, code case closed, occupancy restored, or a sale/transfer
              (the '*' wildcard) that resolves the whole parcel's open distress.
  DECAY       a signal's weight halves every half_life_days, so a 2-week-old code
              case outweighs a 2-year-old one (decay_factor in (0, 1]).
  EXPIRY      a signal untouched for expire_after_days is stale and drops out.

Resolution and expiry take a signal out of 'active'; decay just scales it. Run
this as a PRE-FILTER on the signals stream before scoring: `apply()` returns only
the still-live signals, each annotated with its lifecycle state and decay_factor,
so the existing five-score naturally stops counting resolved/expired leads. It's
the mirror of the catalyst calendar — decay retires stale leads, the calendar
surfaces ripening ones. Pass --today for determinism. Pure stdlib.
"""

import argparse
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _resolvers(rules):
    """signal_type -> set of resolving event types (including the wildcard set)."""
    wild = set(rules["resolves"].get("*", []))
    out = {}
    for st, evs in rules["resolves"].items():
        if st == "*":
            continue
        out[st] = set(evs) | wild
    return out, wild


def half_life(signal_type, rules):
    return rules["half_life_days"].get(signal_type, rules["half_life_days"]["default"])


def expire_after(signal_type, rules):
    return rules["expire_after_days"].get(signal_type, rules["expire_after_days"]["default"])


def classify(signal, parcel_events, rules, today):
    """Return the lifecycle verdict for one signal given all events on its parcel.
    verdict: {state, decay_factor, age_days, resolved_by?}."""
    resolvers, wild = _resolvers(rules)
    st = signal.get("signal_type", "")
    sdate = _date(signal["date"]) if signal.get("date") else None

    # RESOLUTION: a resolving event that occurred at/after the signal's date.
    resolving_types = resolvers.get(st, wild)
    if sdate is not None:
        for e in parcel_events:
            if e.get("signal_type") in resolving_types and e.get("date") \
                    and _date(e["date"]) >= sdate:
                return {"state": "resolved", "decay_factor": 0.0,
                        "age_days": (today - sdate).days,
                        "resolved_by": e["signal_type"], "resolved_on": e["date"][:10]}

    # No date -> treat as active-undated, full weight, can't decay or expire.
    if sdate is None:
        return {"state": "active", "decay_factor": 1.0, "age_days": None}

    age = (today - sdate).days
    if age < 0:
        age = 0
    # EXPIRY
    if age > expire_after(st, rules):
        return {"state": "expired", "decay_factor": 0.0, "age_days": age}
    # DECAY
    factor = round(0.5 ** (age / half_life(st, rules)), 4)
    return {"state": "active", "decay_factor": factor, "age_days": age}


def lifecycle(signals, rules, today):
    """Verdict for every signal, keyed by parcel so resolvers see their parcel."""
    by_apn = {}
    for s in signals:
        by_apn.setdefault((s.get("apn") or "").strip(), []).append(s)
    out = []
    for apn, evs in by_apn.items():
        for s in evs:
            v = classify(s, evs, rules, today)
            out.append({**s, **v})
    return out


def apply(signals, rules, today):
    """Pre-filter for the score: keep only still-live signals, annotated with state
    and decay_factor. Resolved and expired signals are dropped (and returned
    separately so a run can report what fell off)."""
    verdicts = lifecycle(signals, rules, today)
    live = [v for v in verdicts if v["state"] == "active"]
    dropped = [v for v in verdicts if v["state"] != "active"]
    return live, dropped


def decayed_points(points, decay_factor):
    """Optional helper: scale a signal's points by its decay. Kept separate so the
    score can opt in without the pre-filter forcing it."""
    return round(points * decay_factor, 2)


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="Opportunity-Decay lead lifecycle")
    ap.add_argument("--signals", required=True, help="signals/events JSONL (apn, signal_type, date)")
    ap.add_argument("--rules", default=os.path.join(HERE, "lifecycle_rules.json"))
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (determinism)")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    today = _date(args.today)
    signals = load_jsonl(args.signals)

    live, dropped = apply(signals, rules, today)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "live_signals.jsonl"), "w", encoding="utf-8") as f:
        for s in live:
            f.write(json.dumps(s) + "\n")
    with open(os.path.join(args.outdir, "retired_signals.jsonl"), "w", encoding="utf-8") as f:
        for s in dropped:
            f.write(json.dumps(s) + "\n")

    resolved = [d for d in dropped if d["state"] == "resolved"]
    expired = [d for d in dropped if d["state"] == "expired"]
    print(f"Signals in: {len(signals)}")
    print(f"  live (kept)    : {len(live)}")
    print(f"  resolved (drop): {len(resolved)}")
    print(f"  expired  (drop): {len(expired)}")
    if resolved:
        print("\nNewly resolved (leads that should drop off the list):")
        for d in resolved[:10]:
            print(f"  APN {d.get('apn',''):<14} {d['signal_type']:<22} resolved by "
                  f"{d['resolved_by']} on {d.get('resolved_on','')}")
    faded = sorted([s for s in live if s["decay_factor"] < 0.5], key=lambda s: s["decay_factor"])
    if faded:
        print(f"\nFading (decay_factor < 0.5, weakening but still live): {len(faded)}")
        for s in faded[:5]:
            print(f"  APN {s.get('apn',''):<14} {s['signal_type']:<22} "
                  f"factor {s['decay_factor']} (age {s['age_days']}d)")
    print(f"\nWrote {args.outdir}/live_signals.jsonl, retired_signals.jsonl")


if __name__ == "__main__":
    main()
