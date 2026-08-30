"""Pin the derived-history signals. Run: python3 test_derive.py"""

import json
import os
from datetime import datetime

from derive import tax_delinquency_multiyear, complaint_velocity_311, stalled_subdivision

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "derive_rules.json"), encoding="utf-8"))
TODAY = datetime(2026, 8, 19)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # --- tax staging ---
    hist = [
        # A: 3 consecutive delinquent years with rising balance -> multiyear, hot
        {"apn": "A", "year": 2024, "delinquent": "yes", "balance": "1200"},
        {"apn": "A", "year": 2025, "delinquent": "true", "balance": "2600"},
        {"apn": "A", "year": 2026, "delinquent": "1", "balance": "4100"},
        # B: single delinquent year -> no signal
        {"apn": "B", "year": 2026, "delinquent": "yes", "balance": "500"},
        # C: two delinquent years but with a paid year between -> run is only 1
        {"apn": "C", "year": 2024, "delinquent": "yes", "balance": "800"},
        {"apn": "C", "year": 2025, "delinquent": "no", "balance": "0"},
        {"apn": "C", "year": 2026, "delinquent": "yes", "balance": "900"},
    ]
    ts = tax_delinquency_multiyear(hist, RULES, 2026)
    ts_apns = sorted(s["apn"] for s in ts)
    ok &= check("only A stages multiyear", ts_apns, ["A"])
    a = ts[0]
    ok &= check("A run is 3 consecutive years", a["consecutive_years"], 3)
    ok &= check("A flags rising balance", a["rising_balance"], True)
    ok &= check("A evidence marks foreclosure-ripe", "foreclosure-ripe" in a["source_url"], True)

    # --- 311 velocity ---
    comp = [
        # D: 4 in last year, 1 in prior year -> rising -> fires
        {"apn": "D", "date": "2026-07-01"}, {"apn": "D", "date": "2026-05-01"},
        {"apn": "D", "date": "2026-02-01"}, {"apn": "D", "date": "2025-10-01"},
        {"apn": "D", "date": "2025-03-01"},
        # E: 4 last year but 4 prior year too -> not rising -> no fire
        {"apn": "E", "date": "2026-07-01"}, {"apn": "E", "date": "2026-05-01"},
        {"apn": "E", "date": "2026-02-01"}, {"apn": "E", "date": "2025-10-01"},
        {"apn": "E", "date": "2025-06-01"}, {"apn": "E", "date": "2025-04-01"},
        {"apn": "E", "date": "2025-02-01"}, {"apn": "E", "date": "2024-12-01"},
        # F: only 2 recent -> below min_recent -> no fire
        {"apn": "F", "date": "2026-07-01"}, {"apn": "F", "date": "2026-05-01"},
    ]
    cv = complaint_velocity_311(comp, RULES, TODAY)
    cv_apns = sorted(s["apn"] for s in cv)
    ok &= check("only D fires on rising velocity", cv_apns, ["D"])
    ok &= check("D counts recent", cv[0]["recent"], 4)

    # --- stalled subdivision ---
    plats = [
        {"apn": "G", "plat_date": "2023-01-01", "lots": 24},   # old plat
        {"apn": "H", "plat_date": "2026-06-01", "lots": 10},   # fresh plat -> not stalled
    ]
    permits = [
        {"apn": "H", "permit_type": "building", "date": "2026-07-01"},  # H is building out
        # G has no vertical permits since its plat
        {"apn": "G", "permit_type": "grading", "date": "2023-06-01"},   # not vertical
    ]
    ss = stalled_subdivision(plats, permits, RULES, TODAY)
    ss_apns = sorted(s["apn"] for s in ss)
    ok &= check("only G is a stalled subdivision", ss_apns, ["G"])
    ok &= check("G evidence names the plat age", "mo ago" in ss[0]["source_url"], True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
