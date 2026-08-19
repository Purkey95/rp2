"""Pin the Owner Distress Score behavior. Run: python3 test_score.py"""

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

    # Owner-occupied parcel whose only signal is RESOLVED -> no lead (excluded).
    ok &= check("resolved-only parcel excluded", "16700423" in by_apn, False)

    # Top lead stacks all four categories -> full stacking bonus, immediate band.
    top = by_apn["07104521"]
    ok &= check("stacked lead band", top["band"], "immediate")
    ok &= check("stacked lead scores high", top["score"] >= 90, True)
    ok &= check("four categories", sorted(top["categories"]),
                ["behavioral", "deterioration", "financial", "ownership"])
    ok &= check("stacking bonus capped", top["stacking_bonus"], 18)

    # Every contributing point carries evidence (provenance or computed reason).
    ok &= check("all signals have evidence",
                all(s["evidence"] for s in top["signals"]), True)

    # Sourced signal keeps its source URL as evidence.
    tax = next(s for s in top["signals"] if s["signal"] == "tax_delinquency")
    ok &= check("sourced evidence is url", tax["evidence"].startswith("http"), True)

    # Behavioral signal fired from portfolio (owner holds 3, sold one in 2025).
    ok &= check("portfolio size", top["portfolio_size"], 3)
    ok &= check("recently-sold-another present",
                any(s["signal"] == "recently_sold_another" for s in top["signals"]), True)

    # Derived signals: absentee + out_of_state + long tenure + high-equity proxy.
    names = {s["signal"] for s in top["signals"]}
    ok &= check("derived signals present",
                {"absentee", "out_of_state", "long_tenure_20y", "high_equity_proxy"} <= names, True)

    # High-equity proxy does NOT fire without a mortgage figure (never guessed).
    uptown = by_apn["11902388"]
    ok &= check("no equity proxy without mortgage",
                any(s["signal"] == "high_equity_proxy" for s in uptown["signals"]), False)

    # Results sorted descending by score.
    ok &= check("sorted desc", all(res[i]["score"] >= res[i + 1]["score"] for i in range(len(res) - 1)), True)

    # Owner normalization groups the LLC's parcels, keeps entity suffix.
    ok &= check("owner key keeps entity", normalize_owner("CAROLINA HOLDINGS LLC"), "carolina holdings llc")
    ok &= check("owner key county order", normalize_owner("PATEL, ANJALI"), "anjali patel")

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
