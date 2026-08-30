"""Pin the information-arbitrage derivations. Run: python3 test_info.py"""

from info import legal_lot_count, tax_lot_legal_lot_mismatch, address_anomaly_multiunit


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # --- legal lot counting ---
    ok &= check("explicit legal_lot_count", legal_lot_count({"legal_lot_count": "3"}), 3)
    ok &= check("plural LOTS with numbers",
                legal_lot_count({"legal_description": "LOTS 4 5 AND 6 BLOCK A GREENWOOD"}), 3)
    ok &= check("two 'LOT n' patterns",
                legal_lot_count({"legal_description": "LOT 7 AND LOT 8 SEC 2"}), 2)
    ok &= check("single lot is 1", legal_lot_count({"legal_description": "LOT 12 BLOCK C OAKHURST"}), 1)
    ok &= check("no description is 1", legal_lot_count({}), 1)

    parcels = [
        {"apn": "M1", "legal_lot_count": "3"},
        {"apn": "M2", "legal_description": "LOTS 4 5 AND 6 BLOCK A"},
        {"apn": "S1", "legal_description": "LOT 12 BLOCK C"},   # single -> no fire
    ]
    tl = tax_lot_legal_lot_mismatch(parcels)
    ok &= check("only multi-lot parcels fire", sorted(s["apn"] for s in tl), ["M1", "M2"])
    ok &= check("reports the lot count", [s for s in tl if s["apn"] == "M1"][0]["legal_lots"], 3)

    # --- address / permit multi-unit anomaly ---
    aparcels = [
        {"apn": "N1", "assessor_units": "1"},   # 3 addresses -> anomaly
        {"apn": "N2", "assessor_units": "1"},   # duplex permit -> anomaly
        {"apn": "N3", "assessor_units": "2"},   # 2 addresses, matches assessor -> no fire
        {"apn": "N4", "assessor_units": "4"},   # real 4-plex, 4 addresses -> no fire
    ]
    addresses = [
        {"apn": "N1", "address": "100 MAIN ST"}, {"apn": "N1", "address": "100 MAIN ST A"},
        {"apn": "N1", "address": "100 MAIN ST B"},
        {"apn": "N3", "address": "5 OAK A"}, {"apn": "N3", "address": "5 OAK B"},
        {"apn": "N4", "address": "9 ELM 1"}, {"apn": "N4", "address": "9 ELM 2"},
        {"apn": "N4", "address": "9 ELM 3"}, {"apn": "N4", "address": "9 ELM 4"},
    ]
    permits = [{"apn": "N2", "description": "DUPLEX CONVERSION - ADD SECOND UNIT"}]

    aa = address_anomaly_multiunit(aparcels, addresses, permits)
    aa_apns = sorted(s["apn"] for s in aa)
    ok &= check("N1 (3 addr) and N2 (duplex permit) fire", aa_apns, ["N1", "N2"])
    ok &= check("N3 (addr == assessor) does not fire", "N3" not in aa_apns, True)
    ok &= check("N4 (real fourplex) does not fire", "N4" not in aa_apns, True)
    ok &= check("N1 implied units from addresses", [s for s in aa if s["apn"] == "N1"][0]["implied_units"], 3)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
