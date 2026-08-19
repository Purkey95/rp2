#!/usr/bin/env python3
"""Permit type-mix — a market signal computed from data we ALREADY pull.

Not permit COUNT (that's supply) but permit MIX: the ratio of new-construction to
repair/maintenance permits tells you whether capital in a submarket is offensive
(building) or defensive (patching what's there). A rising repair/trade share is a
late-cycle / cautious tell. Because our sourcing adapters already classify each
permit by type (`type_of_work` -> signal_type), this needs no new data source.

Reads the sourcing adapters' signals JSONL; buckets permit signal_types; reports
shares + a read. Feeds the Market Timing layer (WHEN/WHERE), per submarket.

    python3 permit_mix.py --signals ../monitorclt_sourcing/out/signals.jsonl
"""

import argparse
import json
from collections import Counter

# Permit signal_types (from the sourcing type_maps) bucketed by what the capital is doing.
BUCKETS = {
    "new": {"new_construction", "new_single_family", "new_multi_family", "modular_home",
            "building_permit"},
    "repair": {"renovation_permit", "addition_permit", "residential_permit"},
    "trade": {"electrical_permit", "mechanical_permit", "plumbing_permit",
              "equipment_changeout", "plan_review"},
    "demo": {"demolition_permit"},
}
PERMIT_TYPES = set().union(*BUCKETS.values())


def classify(signal_type):
    for bucket, types in BUCKETS.items():
        if signal_type in types:
            return bucket
    return "other"


def permit_mix(signals):
    counts = Counter()
    for s in signals:
        st = s.get("signal_type", "")
        if st in PERMIT_TYPES:
            counts[classify(st)] += 1
    total = sum(counts.values())
    if not total:
        return {"total": 0, "read": "no permit signals in input"}
    shares = {b: round(counts.get(b, 0) / total * 100, 1) for b in ("new", "repair", "trade", "demo")}
    defensive = shares["repair"] + shares["trade"]
    offensive = shares["new"]
    if offensive >= defensive + 10:
        read = "offensive: capital is building (growth phase)"
    elif defensive >= offensive + 10:
        read = "defensive: capital is patching, not building (cautious / late-cycle)"
    else:
        read = "balanced new vs repair"
    return {"total": total, "counts": dict(counts), "shares_pct": shares,
            "defensive_pct": round(defensive, 1), "offensive_pct": round(offensive, 1),
            "read": read}


def load_signals(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="Permit type-mix from sourced signals")
    ap.add_argument("--signals", required=True)
    args = ap.parse_args()
    result = permit_mix(load_signals(args.signals))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
