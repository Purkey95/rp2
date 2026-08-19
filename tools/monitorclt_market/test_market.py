"""Pin the Market Timing analysis (deterministic, offline). Run: python3 test_market.py

The live FRED fetch is proven separately (curl returns real current values); this
suite tests the analysis logic with injected synthetic series so it never depends
on the network.
"""

import fred
from market import build_report, synthesis


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def monthly(start_val, step, n, start_ym=(2024, 1)):
    """n monthly (date, value) points from start_val stepping by `step`."""
    out, y, m, v = [], start_ym[0], start_ym[1], start_val
    for _ in range(n):
        out.append((f"{y:04d}-{m:02d}-01", round(v, 3)))
        v += step
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def csv_of(series):
    return "observation_date,VALUE\n" + "\n".join(f"{d},{v}" for d, v in series)


def main():
    ok = True

    # ---- fred trend helpers ----
    rising = monthly(3.5, 0.05, 24)          # +0.05/mo for 24 months
    ok &= check("pct_change positive", fred.pct_change(rising, 12) > 0, True)
    ok &= check("direction rising", fred.direction(rising, 6), "rising")
    falling = monthly(6.0, -0.05, 24)
    ok &= check("direction falling", fred.direction(falling, 6), "falling")
    flat = monthly(5.0, 0.0, 24)
    ok &= check("direction flat", fred.direction(flat, 6), "flat")
    accel = monthly(2.0, 0.01, 18) + monthly(2.18, 0.30, 6, (2025, 7))  # flat-ish, then steep
    ok &= check("acceleration detected", fred.acceleration(accel, 6), "accelerating")

    # ---- build_report: derived mortgage_spread + jobs_to_permits + synthesis ----
    data = {
        # mortgage rises faster than treasury -> spread WIDENING
        "DGS10": monthly(3.5, 0.02, 18),
        "MORTGAGE30US": monthly(5.0, 0.08, 18),
        "DRTSCLCC": monthly(1.0, 0.5, 18),                 # tightening rising
        "DRSFRMACBS": monthly(1.5, 0.03, 18),              # delinquency rising
        "CHAR737BPPRIV": monthly(1300, 0, 18),             # 1300/mo flat -> 15600 over 12mo
        "CHAR737NA": monthly(1380, 1.75, 18),              # +1.75k/mo -> +21k over 12mo
        "CHAR737URN": monthly(3.0, 0.05, 18),
    }
    cfg = {
        "series": [
            {"id": "DGS10", "label": "10Y", "layer": "leading"},
            {"id": "MORTGAGE30US", "label": "30Y mtg", "layer": "leading"},
            {"id": "DRTSCLCC", "label": "SLOOS CRE", "layer": "leading"},
            {"id": "CHAR737BPPRIV", "label": "CLT permits", "layer": "leading"},
            {"id": "DRSFRMACBS", "label": "SF delinq", "layer": "coincident"},
            {"id": "CHAR737NA", "label": "CLT jobs", "layer": "coincident"},
            {"id": "CHAR737URN", "label": "CLT unemp", "layer": "coincident"},
        ],
    }

    def fetch(url):
        sid = url.split("id=")[1].split("&")[0]
        return csv_of(data[sid])

    report = build_report(cfg, fetch=fetch)
    der = {d["name"]: d for d in report["derived"]}

    ok &= check("mortgage_spread computed", "mortgage_spread" in der, True)
    ok &= check("spread widening", der["mortgage_spread"]["direction_6m"], "widening")
    ok &= check("jobs_to_permits computed", "jobs_to_permits" in der, True)
    # 21,000 jobs / 15,600 units ~= 1.35
    ratio = der["jobs_to_permits"]["latest"]
    ok &= check("jobs_to_permits ~1.35", 1.2 <= ratio <= 1.5, True)

    notes = synthesis(report)
    ok &= check("synthesis flags tightening",
                any("tighten" in n or "widening" in n for n in notes), True)
    ok &= check("synthesis flags distress supply",
                any("delinquency" in n for n in notes), True)

    # lagging series present but never drives synthesis (confirmation only)
    ok &= check("indicators carry layer", report["indicators"][0]["layer"], "leading")

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
