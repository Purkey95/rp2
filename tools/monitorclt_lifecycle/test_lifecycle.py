"""Pin the Opportunity-Decay lifecycle. Run: python3 test_lifecycle.py"""

import json
import os
from datetime import datetime

from lifecycle import classify, apply, lifecycle, decayed_points, half_life

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "lifecycle_rules.json"), encoding="utf-8"))
TODAY = datetime(2026, 8, 19)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # RESOLUTION: tax delinquency paid afterward -> resolved
    ev = [
        {"apn": "A", "signal_type": "tax_delinquency", "date": "2025-06-01"},
        {"apn": "A", "signal_type": "tax_paid", "date": "2026-02-01"},
    ]
    v = classify(ev[0], ev, RULES, TODAY)
    ok &= check("paid tax delinquency resolves", v["state"], "resolved")
    ok &= check("resolved records the resolver", v["resolved_by"], "tax_paid")

    # a payment BEFORE the delinquency does not resolve a later one
    ev2 = [
        {"apn": "A", "signal_type": "tax_delinquency", "date": "2026-06-01"},
        {"apn": "A", "signal_type": "tax_paid", "date": "2025-01-01"},
    ]
    ok &= check("earlier payment does not resolve later delinquency",
                classify(ev2[0], ev2, RULES, TODAY)["state"], "active")

    # WILDCARD: a sale resolves ANY open distress on the parcel
    ev3 = [
        {"apn": "B", "signal_type": "code_violation", "date": "2026-01-01"},
        {"apn": "B", "signal_type": "sold", "date": "2026-05-01"},
    ]
    ok &= check("sale resolves unrelated open distress",
                classify(ev3[0], ev3, RULES, TODAY)["state"], "resolved")

    # EXPIRY: a code violation older than its expire_after (730d) drops out
    old = [{"apn": "C", "signal_type": "code_violation", "date": "2023-01-01"}]
    ok &= check("stale code violation expires", classify(old[0], old, RULES, TODAY)["state"], "expired")

    # DECAY: a signal exactly one half-life old has factor ~0.5
    hl = half_life("code_violation", RULES)  # 365
    from datetime import timedelta
    one_hl = [{"apn": "D", "signal_type": "code_violation",
               "date": (TODAY - timedelta(days=hl)).strftime("%Y-%m-%d")}]
    v = classify(one_hl[0], one_hl, RULES, TODAY)
    ok &= check("one half-life -> factor ~0.5", abs(v["decay_factor"] - 0.5) < 0.01, True)
    ok &= check("one half-life still active", v["state"], "active")

    # fresh signal -> factor ~1.0
    fresh = [{"apn": "E", "signal_type": "code_violation", "date": "2026-08-15"}]
    ok &= check("fresh signal near full weight", classify(fresh[0], fresh, RULES, TODAY)["decay_factor"] > 0.98, True)

    # apply(): resolved/expired removed, live kept
    stream = ev + ev3 + old + fresh
    live, dropped = apply(stream, RULES, TODAY)
    live_apns = sorted({s["apn"] for s in live})
    ok &= check("apply keeps only live parcels' signals (D dropped as tax_paid isn't a distress signal here)",
                "E" in live_apns and "C" not in live_apns, True)
    ok &= check("dropped includes resolved and expired", len(dropped) >= 3, True)

    # decayed_points helper
    ok &= check("decayed_points scales", decayed_points(20, 0.5), 10.0)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
