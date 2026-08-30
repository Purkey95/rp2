"""Pin the Catalyst Calendar. Run: python3 test_catalyst.py"""

import json
import os

from catalyst import project_catalysts, score_parcels, upcoming_within, imminence

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "catalyst_rules.json"), encoding="utf-8"))

from datetime import datetime
TODAY = datetime(2026, 8, 19)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # imminence: closer catalysts weigh more, past ones are 0
    ok &= check("imminence 20d = 1.0", imminence(20, RULES), 1.0)
    ok &= check("imminence 50d = 0.8", imminence(50, RULES), 0.8)
    ok &= check("imminence past = 0", imminence(-5, RULES), 0.0)
    ok &= check("imminence far = floor", imminence(400, RULES), RULES["horizon_floor"])

    rows = [
        # explicit future catalyst inside horizon
        {"apn": "A", "catalyst_type": "rezoning_hearing", "date": "2026-09-15", "source_url": "docket#1"},
        # explicit far-future beyond default horizon -> dropped
        {"apn": "A", "catalyst_type": "ground_lease_expiration", "date": "2030-01-01", "source_url": "rod#9"},
        # explicit already-past -> dropped (belongs to timeline, not calendar)
        {"apn": "B", "catalyst_type": "auction_date", "date": "2026-01-01", "source_url": "old"},
        # derived: tax_delinquency -> foreclosure eligibility ~730d later; dated so the
        # projected milestone lands mid-horizon (~194d out), inside 365 but past 60.
        {"apn": "C", "signal_type": "tax_delinquency", "date": "2025-03-01", "source_url": "tax"},
        # signal with no derived rule -> nothing
        {"apn": "D", "signal_type": "absentee", "date": "2026-08-01", "source_url": "x"},
    ]

    proj = project_catalysts(rows, RULES, TODAY)
    apns = sorted({p["apn"] for p in proj})
    ok &= check("only A and C project catalysts", apns, ["A", "C"])
    ok &= check("A's far-future ground lease dropped",
                all(p["catalyst_type"] != "ground_lease_expiration" for p in proj), True)
    ok &= check("B's past auction dropped", all(p["apn"] != "B" for p in proj), True)
    c_cat = [p for p in proj if p["apn"] == "C"][0]
    ok &= check("C derived a tax_foreclosure_eligibility", c_cat["catalyst_type"], "tax_foreclosure_eligibility")
    ok &= check("C catalyst is flagged derived", c_cat["derived"], True)

    results = score_parcels(rows, RULES, TODAY)
    a = [r for r in results if r["apn"] == "A"][0]
    ok &= check("A next catalyst is the hearing", a["next_catalyst"], "rezoning_hearing")
    ok &= check("A horizon > 0", a["catalyst_horizon"] > 0, True)

    # headline query: A's hearing is ~27d out, C's eligibility is ~1yr out
    within60 = upcoming_within(results, 60)
    ok &= check("A is within 60 days", any(r["apn"] == "A" for r in within60), True)
    ok &= check("C is NOT within 60 days", all(r["apn"] != "C" for r in within60), True)

    # sooner catalyst sorts first
    ok &= check("results sorted by imminence", results[0]["apn"], "A")

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
