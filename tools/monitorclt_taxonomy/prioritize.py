#!/usr/bin/env python3
"""Turn the signal taxonomy into a build order — which signals to build first.

The point of a taxonomy isn't completeness, it's prioritization. This ranks the
not-yet-built signals by a build-priority score so we build the 25-40 that matter
instead of trying to ingest everything:

    priority = value x reliability x accessibility / effort

- value/reliability/effort come from the taxonomy (1-5).
- accessibility is derived from availability: data we already have (wired/derived)
  or can get free (env-free/free-add) ranks far above paid feeds and browser-only
  public records — because "high value, can't get it cheaply" is not build-next.
- not-obtainable is excluded; sensitive-legality is flagged, not auto-excluded.

Also surfaces QUICK WINS: available + high value + low effort.
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

ACCESSIBILITY = {
    "wired": 1.0, "derived": 1.0, "env-free": 0.9, "free-add": 0.85,
    "sos-paid": 0.5, "mls-paid": 0.45, "rod-browser": 0.4, "court-browser": 0.35,
    "not-obtainable": 0.0,
}


def priority(sig):
    acc = ACCESSIBILITY.get(sig["availability"], 0.5)
    if acc == 0 or sig["effort"] == 0:
        return 0.0
    return round(sig["value"] * sig["reliability"] * acc / sig["effort"], 2)


def main():
    ap = argparse.ArgumentParser(description="Prioritize the signal taxonomy")
    ap.add_argument("--taxonomy", default=os.path.join(HERE, "signal_taxonomy.json"))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    tax = json.load(open(args.taxonomy, encoding="utf-8"))
    signals = tax["signals"]
    built = [s for s in signals if s["status"] == "built"]
    todo = [s for s in signals if s["status"] != "built" and s["availability"] != "not-obtainable"]
    for s in todo:
        s["priority"] = priority(s)
    todo.sort(key=lambda s: -s["priority"])

    print(f"Taxonomy: {len(signals)} signals · {len(built)} built · {len(todo)} buildable-and-pending\n")

    print(f"=== BUILD NEXT (top {args.top} by value x reliability x accessibility / effort) ===")
    for s in todo[:args.top]:
        flag = "  [SENSITIVE]" if s["legality"] == "sensitive" else ""
        print(f"  {s['priority']:>5}  {s['id']:<34} {s['availability']:<13} "
              f"v{s['value']} r{s['reliability']} e{s['effort']}  ({s['family']}){flag}")

    quick = [s for s in todo if s["availability"] in ("wired", "derived", "env-free", "free-add")
             and s["value"] >= 4 and s["effort"] <= 3]
    print(f"\n=== QUICK WINS (free/derived data, high value, low effort) — {len(quick)} ===")
    for s in sorted(quick, key=lambda s: -s["priority"]):
        print(f"  {s['priority']:>5}  {s['id']:<34} {s['availability']:<10} ({s['family']})")

    # what's gated behind data we don't have cheaply — high value but deprioritized
    gated = [s for s in todo if s["availability"] in ("sos-paid", "mls-paid", "rod-browser", "court-browser")
             and s["value"] >= 4]
    print(f"\n=== HIGH VALUE, GATED (worth it, but need a credential / records-request / paid feed) — {len(gated)} ===")
    for s in sorted(gated, key=lambda s: -s["value"]):
        print(f"  v{s['value']}  {s['id']:<34} {s['availability']:<13} ({s['family']})")

    fam = {}
    for s in signals:
        fam.setdefault(s["family"], {"built": 0, "total": 0})
        fam[s["family"]]["total"] += 1
        if s["status"] == "built":
            fam[s["family"]]["built"] += 1
    print("\n=== COVERAGE BY FAMILY ===")
    for f, c in sorted(fam.items()):
        print(f"  {c['built']}/{c['total']:<3} {f}")


if __name__ == "__main__":
    main()
