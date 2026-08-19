"""Pin the Seller Opportunity Score (five components → combined). Run: python3 test_score.py"""

import json
import os

from score import score_parcels, load_signals, load_parcels
from entity import normalize_owner

HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True
    cfg = json.load(open(os.path.join(HERE, "weights.json"), encoding="utf-8"))
    signals = load_signals(os.path.join(HERE, "sample", "signals.jsonl"))
    parcels = load_parcels(os.path.join(HERE, "sample", "parcels.csv"))
    res = score_parcels(signals, parcels, cfg, 2026)
    by_apn = {r["apn"]: r for r in res}

    # Resolved-only, owner-occupied parcel -> no lead.
    ok &= check("resolved-only parcel excluded", "16700423" in by_apn, False)

    top = by_apn["07104521"]
    # Combined = capped sum of contributions across dimensions.
    ok &= check("combined score", top["seller_opportunity_score"], 80)
    ok &= check("band", top["band"], "priority")

    # Five component scores, computed per dimension.
    comp = top["components"]
    ok &= check("has five dimensions", sorted(comp.keys()),
                ["disposition_probability", "financial_distress", "landlord_fatigue",
                 "ownership_transition", "property_distress"])
    ok &= check("financial component (tax20+equity10)", comp["financial_distress"], 30)
    ok &= check("property component (code16)", comp["property_distress"], 16)
    ok &= check("ownership component (abs8+oos8+tenure6)", comp["ownership_transition"], 22)
    ok &= check("disposition component (sold12)", comp["disposition_probability"], 12)
    ok &= check("landlord component zero", comp["landlord_fatigue"], 0)
    ok &= check("dimensions firing", top["dimensions_firing"], 4)

    # Every contributing point carries a dimension + evidence (provenance).
    ok &= check("signals carry dimension", all(s["dimension"] for s in top["signals"]), True)
    ok &= check("signals carry evidence", all(s["evidence"] for s in top["signals"]), True)
    tax = next(s for s in top["signals"] if s["signal"] == "tax_delinquency")
    ok &= check("sourced evidence is url", tax["evidence"].startswith("http"), True)
    ok &= check("tax mapped to financial", tax["dimension"], "financial_distress")

    # Behavioral signal from portfolio (owner holds 3, sold one recently).
    ok &= check("recently-sold-another present",
                any(s["signal"] == "recently_sold_another" for s in top["signals"]), True)

    # High-equity proxy needs a mortgage figure (never guessed).
    uptown = by_apn["11902388"]
    ok &= check("no equity proxy without mortgage",
                any(s["signal"] == "high_equity_proxy" for s in uptown["signals"]), False)

    # Sorted by combined score desc.
    ok &= check("sorted desc",
                all(res[i]["seller_opportunity_score"] >= res[i + 1]["seller_opportunity_score"]
                    for i in range(len(res) - 1)), True)

    ok &= check("owner key keeps entity", normalize_owner("CAROLINA HOLDINGS LLC"), "carolina holdings llc")

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
