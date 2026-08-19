"""Pin the records-based valuation engine. Run: python3 test_valuation.py"""

import json
import os
from datetime import datetime

from valuation import (miles_between, select_comps, adjust_comp, value_subject)

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "valuation_rules.json"), encoding="utf-8"))
TODAY = datetime(2026, 8, 19)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def approx(label, got, want, tol):
    ok = abs(got - want) <= tol
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r} (~{want}±{tol})")
    return ok


def main():
    ok = True

    # distance sanity: ~0 for same point, None without coords
    ok &= check("distance None without coords", miles_between({}, {"lat": 35, "lon": -80}), None)
    ok &= approx("distance same point ~0", miles_between({"lat": 35.2, "lon": -80.8},
                                                         {"lat": 35.2, "lon": -80.8}), 0.0, 0.001)

    subject = {"apn": "SUBJ", "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2,
               "lot_sqft": 8000, "year_built": 1998, "lat": 35.200, "lon": -80.800}

    pool = [
        # C1: identical except sold 6 months ago -> pure time adjustment
        {"apn": "C1", "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2,
         "lot_sqft": 8000, "year_built": 1998, "lat": 35.201, "lon": -80.800,
         "sale_price": 300000, "sale_date": "2026-02-19"},
        # C2: 200 sqft larger -> should adjust DOWN by 200*90 = 18000
        {"apn": "C2", "property_type": "SFR", "sqft": 1800, "beds": 3, "baths": 2,
         "lot_sqft": 8000, "year_built": 2000, "lat": 35.202, "lon": -80.799,
         "sale_price": 330000, "sale_date": "2026-06-19"},
        # C3: smaller, older, one fewer bath
        {"apn": "C3", "property_type": "SFR", "sqft": 1450, "beds": 3, "baths": 1,
         "lot_sqft": 7500, "year_built": 1990, "lat": 35.199, "lon": -80.802,
         "sale_price": 268000, "sale_date": "2026-05-01"},
        # BAD1: wrong property type -> excluded
        {"apn": "BAD1", "property_type": "CONDO", "sqft": 1600, "beds": 3, "baths": 2,
         "lot_sqft": 8000, "year_built": 1998, "lat": 35.200, "lon": -80.800,
         "sale_price": 250000, "sale_date": "2026-06-01"},
        # BAD2: too old (>365d) -> excluded
        {"apn": "BAD2", "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2,
         "lot_sqft": 8000, "year_built": 1998, "lat": 35.200, "lon": -80.800,
         "sale_price": 250000, "sale_date": "2024-01-01"},
        # BAD3: too far (>1mi) -> excluded (about 3+ miles east)
        {"apn": "BAD3", "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2,
         "lot_sqft": 8000, "year_built": 1998, "lat": 35.200, "lon": -80.750,
         "sale_price": 400000, "sale_date": "2026-06-01"},
    ]

    comps = select_comps(subject, pool, RULES, TODAY)
    comp_apns = sorted(c["apn"] for c in comps)
    ok &= check("only the 3 valid comps selected", comp_apns, ["C1", "C2", "C3"])

    # C1: identical, sold 6mo ago at 300k, 0.4%/mo compounding ~ +2.4% -> ~307,240
    c1 = [c for c in comps if c["apn"] == "C1"][0]
    adj1, grid1 = adjust_comp(subject, c1, RULES, TODAY)
    ok &= approx("C1 adjusted ~ time only", adj1, 300000 * (1.004 ** (c1["_age_days"] / 30.0)), 5)
    ok &= check("C1 has no feature adjustments", all(k not in grid1 for k in ("sqft", "beds", "baths")), True)

    # C2: 200 sqft larger => a -18000 sqft adjustment appears
    c2 = [c for c in comps if c["apn"] == "C2"][0]
    adj2, grid2 = adjust_comp(subject, c2, RULES, TODAY)
    ok &= check("C2 sqft adjusted down 18000", grid2["sqft"], -18000)
    ok &= check("C2 year_built adjusted down (comp newer)", grid2["year_built"] < 0, True)

    result = value_subject(subject, pool, RULES, TODAY)
    ok &= check("subject valued", result["status"], "valued")
    ok &= check("used 3 comps", result["comps_used"], 3)
    # estimate should land near the identical comp's time-adjusted value (~307k), in a sane band
    ok &= approx("estimate in sane band", result["estimated_value"], 307000, 20000)
    ok &= check("confidence present", 0 <= result["confidence"] <= 100, True)

    # offer band = estimate * 0.65..0.80
    ob = result["offer_band"]
    ok &= approx("as-is low = 65%", ob["as_is_low"], result["estimated_value"] * 0.65, 2)
    ok &= approx("as-is high = 80%", ob["as_is_high"], result["estimated_value"] * 0.80, 2)

    # with repairs, 70%-rule MAO appears
    with_repairs = value_subject(subject, pool, RULES, TODAY, repairs=40000)
    ok &= approx("MAO = est*0.7 - repairs", with_repairs["offer_band"]["mao_70_rule"],
                 result["estimated_value"] * 0.70 - 40000, 2)

    # too-thin pool -> insufficient_comps, no crash
    thin = value_subject(subject, pool[:1], RULES, TODAY)
    ok &= check("thin pool reports insufficient", thin["status"], "insufficient_comps")

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
