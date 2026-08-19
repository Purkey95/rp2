"""Pin the parcel-geometry wedges. Run: python3 test_geometry.py"""

import json
import os

from geometry import relationship_value_access, hidden_density_zoning_mismatch, zoning_max_units

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "geometry_rules.json"), encoding="utf-8"))


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # --- relationship_value_access ---
    parcels = [
        # LAND has no frontage; neighbor FRONT has frontage -> LAND is controlled
        {"apn": "LAND", "road_frontage": "no", "neighbors": ["FRONT", "OTHER"]},
        {"apn": "FRONT", "road_frontage": "yes", "neighbors": ["LAND"]},
        {"apn": "OTHER", "road_frontage": "no", "neighbors": ["LAND"]},   # also landlocked, no frontage neighbor
        # NORMAL has its own frontage -> never landlocked
        {"apn": "NORMAL", "road_frontage": "true", "neighbors": ["FRONT"]},
    ]
    rva = relationship_value_access(parcels, RULES)
    rva_apns = sorted(s["apn"] for s in rva)
    ok &= check("only LAND is controlled-access", rva_apns, ["LAND"])
    ok &= check("LAND names FRONT as controller", rva[0]["controllers"], ["FRONT"])
    ok &= check("LAND flagged sole controller", rva[0]["sole_controller"], True)
    ok &= check("OTHER (no frontage neighbor) does not fire", all(s["apn"] != "OTHER" for s in rva), True)
    ok &= check("NORMAL (has frontage) does not fire", all(s["apn"] != "NORMAL" for s in rva), True)

    # --- hidden density ---
    # explicit max units
    ok &= check("explicit zoning_max_units used",
                zoning_max_units({"zoning_max_units": "20"}, RULES["hidden_density"]), 20.0)
    # derived from zoning + acres: R-8 at 2 acres -> 16 units
    ok &= check("derived from zoning x acres",
                zoning_max_units({"zoning": "R-8", "lot_area_acres": "2"}, RULES["hidden_density"]), 16.0)

    dparcels = [
        # zoned R-22MF, 1 acre -> ~22 units, 1 present -> big upside -> fires
        {"apn": "UP", "zoning": "R-22MF", "lot_area_acres": "1", "existing_units": "1"},
        # zoned R-4, 1 acre -> 4 units, 3 present -> only 1 extra -> no fire
        {"apn": "OK", "zoning": "R-4", "lot_area_acres": "1", "existing_units": "3"},
        # no zoning info -> skip
        {"apn": "UNK", "existing_units": "1"},
    ]
    hd = hidden_density_zoning_mismatch(dparcels, RULES)
    hd_apns = sorted(s["apn"] for s in hd)
    ok &= check("only UP shows hidden density", hd_apns, ["UP"])
    ok &= check("UP reports the unit upside", hd[0]["extra_units"], 21)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
