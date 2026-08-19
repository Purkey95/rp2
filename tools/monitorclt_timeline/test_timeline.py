"""Pin the Temporal Signal Engine. Run: python3 test_timeline.py"""

import json
import os

from timeline import (score_timeline, load_events, by_parcel, recency_weight, _date)

HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True
    rules = json.load(open(os.path.join(HERE, "rules.json"), encoding="utf-8"))
    # severity map fixed here so the test doesn't depend on the score module.
    severity = {"code_violation": 16, "failed_inspection": 15, "demolition_permit": 20,
                "fire_damage": 20, "renovation_permit": 6, "mechanics_lien": 16}
    today = _date("2026-08-19")
    groups = by_parcel(load_events(os.path.join(HERE, "sample", "events.jsonl")))
    R = {apn: score_timeline(evs, rules, severity, today) for apn, evs in groups.items()}

    # recency tiers
    ok &= check("recent event full weight", recency_weight(30, rules), 1.0)
    ok &= check("old event floored", recency_weight(2000, rules), 0.1)

    a, b = R["07104521"], R["11902388"]  # identical event TYPES; A clustered, B spread
    ok &= check("clustered lead scores high", a["why_now"] >= 70, True)
    ok &= check("clustered lead flagged cluster", a["velocity_cluster"], True)
    ok &= check("escalation code->demolition fires", any(e["name"] == "code_to_demolition" for e in a["escalations"]), True)

    ok &= check("spread lead scores low", b["why_now"] < 40, True)
    ok &= check("spread lead no cluster", b["velocity_cluster"], False)
    ok &= check("spread lead no escalation (window exceeded)", b["escalations"], [])
    # the whole point: same event types, temporal shape drives a 3x difference
    ok &= check("temporal shape dominates (A >> B)", a["why_now"] > b["why_now"] * 2.5, True)

    # failed-flip escalation chain
    ok &= check("failed_flip fires", any(e["name"] == "failed_flip" for e in R["05512304"]["escalations"]), True)

    # negative space: fire with no repair permit
    ok &= check("fire negative-space fires",
                any(n["name"] == "fire_no_repair" for n in R["09330188"]["negative_space"]), True)

    # narrative is the dated timeline
    ok &= check("narrative is dated events", a["narrative"][0].startswith("2026-05-01"), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
