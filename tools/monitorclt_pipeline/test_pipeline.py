"""Pin the end-to-end pipeline. Run: python3 test_pipeline.py"""

from datetime import datetime

from pipeline import load_default_configs, run, apply_market_overlay
from offer_db import _offer_fields

TODAY = datetime(2026, 8, 19)
YEAR = 2026


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True
    cfgs = load_default_configs()

    parcels = [
        # P-1: distressed + absentee + valuation fields -> a real lead that gets valued
        {"apn": "P-1", "owner": "SMITH LLC", "situs_street": "10 OAK ST", "situs_address": "10 OAK ST",
         "mail_street": "99 FAR AWE RD", "situs_state": "NC", "mail_state": "FL",
         "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2, "lot_sqft": 8000,
         "year_built": 1998, "lat": 35.200, "lon": -80.800, "situs_zip": "28208"},
        # P-2: clean parcel, no signals -> should not appear as a lead
        {"apn": "P-2", "owner": "JONES", "situs_street": "22 ELM ST", "situs_address": "22 ELM ST",
         "mail_street": "22 ELM ST", "situs_state": "NC", "mail_state": "NC",
         "property_type": "SFR", "sqft": 1500, "beds": 3, "baths": 2, "lot_sqft": 7500,
         "year_built": 1995, "lat": 35.205, "lon": -80.805, "situs_zip": "28208"},
    ]

    raw_signals = [
        {"apn": "P-1", "signal_type": "tax_delinquency", "bucket": "active", "date": "2026-05-01",
         "source_url": "tax:2026"},
        {"apn": "P-1", "signal_type": "vacancy", "bucket": "active", "date": "2026-06-01",
         "source_url": "code:vac"},
        # a code violation that was RESOLVED (case closed) -> lifecycle must drop it
        {"apn": "P-1", "signal_type": "code_violation", "bucket": "active", "date": "2026-01-01",
         "source_url": "code:1"},
        {"apn": "P-1", "signal_type": "code_case_closed", "bucket": "active", "date": "2026-04-01",
         "source_url": "code:closed"},
    ]

    comps = [
        {"apn": "C1", "property_type": "SFR", "sqft": 1600, "beds": 3, "baths": 2, "lot_sqft": 8000,
         "year_built": 1998, "lat": 35.201, "lon": -80.800, "sale_price": 300000, "sale_date": "2026-02-19"},
        {"apn": "C2", "property_type": "SFR", "sqft": 1650, "beds": 3, "baths": 2, "lot_sqft": 8200,
         "year_built": 2000, "lat": 35.202, "lon": -80.799, "sale_price": 312000, "sale_date": "2026-06-19"},
        {"apn": "C3", "property_type": "SFR", "sqft": 1550, "beds": 3, "baths": 2, "lot_sqft": 7800,
         "year_built": 1996, "lat": 35.199, "lon": -80.802, "sale_price": 292000, "sale_date": "2026-05-01"},
    ]

    result = run(raw_signals, parcels, comps, TODAY, YEAR, cfgs)
    sheet = result["offer_sheet"]

    # P-1 is a lead; P-2 (no signals) is not
    apns = [r["apn"] for r in sheet]
    ok &= check("P-1 is on the offer sheet", "P-1" in apns, True)
    ok &= check("P-2 (clean) is not a lead", "P-2" not in apns, True)

    p1 = [r for r in sheet if r["apn"] == "P-1"][0]
    # lifecycle dropped the resolved code_violation, so it's NOT among P-1's signals
    ok &= check("resolved code_violation dropped from scoring", "code_violation" not in p1["top_signals"], True)
    # tax + vacancy + absentee still score it
    ok &= check("P-1 scored above zero", p1["seller_opportunity_score"] > 0, True)
    ok &= check("P-1 carries a why-now (dated events)", p1["why_now"] is not None, True)

    # valuation ran and produced an offer band
    ok &= check("P-1 was valued", p1["valuation"]["status"], "valued")
    ok &= check("P-1 has an estimated value", p1["valuation"]["estimated_value"] > 0, True)
    ob = p1["valuation"]["offer_band"]
    ok &= check("offer band low < high", ob["as_is_low"] < ob["as_is_high"], True)

    # dropped count reflects the resolved signal
    ok &= check("at least one signal dropped by lifecycle", result["signals_dropped"] >= 1, True)

    # --- market overlay (when/where gate) ---
    # a soft-exit market (>=2 exit cautions) attaches a conservative offer high,
    # leaves the original band intact, and never touches the score.
    soft_market = {
        "indicators": [
            {"id": "DRSFRMACBS", "direction_6m": "rising"},   # delinquency rising
            {"id": "CHAR737URN", "direction_6m": "rising"},   # unemployment rising
        ],
        "derived": [{"name": "mortgage_spread", "direction_6m": "widening"}],
    }
    score_before = p1["seller_opportunity_score"]
    rows2 = [dict(r, valuation=dict(r["valuation"])) if r.get("valuation") else dict(r)
             for r in sheet]
    posture = apply_market_overlay(rows2, soft_market)
    ok &= check("posture surfaced", posture["stance"], "source_aggressively_underwrite_conservatively")
    v = [r for r in rows2 if r["apn"] == "P-1"][0]["valuation"]
    ok &= check("market-adjusted high added", "market_adjusted_high" in v["offer_band"], True)
    ok &= check("adjusted high <= as-is high", v["offer_band"]["market_adjusted_high"] <= v["offer_band"]["as_is_high"], True)
    # a full run with the market report attaches market_context and per-row stance
    rmk = run(raw_signals, parcels, comps, TODAY, YEAR, cfgs, market_report=soft_market)
    ok &= check("run attaches market_context", rmk["market_context"]["stance"],
                "source_aggressively_underwrite_conservatively")
    p1mk = [r for r in rmk["offer_sheet"] if r["apn"] == "P-1"][0]
    ok &= check("score unchanged by market overlay", p1mk["seller_opportunity_score"], score_before)

    # --- db flattening (pure, no psycopg) ---
    flat = _offer_fields(p1mk)
    ok &= check("db flatten pulls estimated_value", flat["estimated_value"] == p1mk["valuation"]["estimated_value"], True)
    ok &= check("db flatten pulls offer_low", flat["offer_low"] == p1mk["valuation"]["offer_band"]["as_is_low"], True)
    ok &= check("db flatten unvalued -> None",
                _offer_fields({"valuation": {"status": "insufficient_comps"}})["estimated_value"], None)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
