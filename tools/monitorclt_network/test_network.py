"""Pin owner-network + neighborhood-contagion. Run: python3 test_network.py"""

from network import owner_network_distress, neighborhood_contagion


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # One owner (SMITH HOLDINGS LLC) with 3 parcels; 2 are distressed -> whole
    # portfolio gets owner_network_distress. A second owner with only 1 distressed
    # parcel does NOT.
    parcels = [
        {"apn": "A", "owner": "SMITH HOLDINGS LLC", "situs_zip": "28208"},
        {"apn": "B", "owner": "Smith Holdings, LLC", "situs_zip": "28208"},   # variant
        {"apn": "C", "owner": "SMITH HOLDINGS LLC", "situs_zip": "28208"},
        {"apn": "D", "owner": "JONES LLC", "situs_zip": "28270"},
        {"apn": "E", "owner": "JONES LLC", "situs_zip": "28270"},
        {"apn": "F", "owner": "Acme LLC", "situs_zip": "28208"},
    ]
    signals = [
        {"apn": "A", "signal_type": "tax_delinquency", "bucket": "active"},
        {"apn": "C", "signal_type": "code_violation", "bucket": "active"},
        {"apn": "F", "signal_type": "foreclosure", "bucket": "active"},
        {"apn": "D", "signal_type": "eviction", "bucket": "active"},
        {"apn": "X", "signal_type": "vacancy", "bucket": "resolved"},   # resolved -> ignored
    ]

    ond = owner_network_distress(signals, parcels, min_distressed=2)
    ond_apns = sorted({s["apn"] for s in ond})
    ok &= check("owner-network fires on all 3 Smith parcels", ond_apns, ["A", "B", "C"])
    ok &= check("Jones (1 distressed) does NOT fire", all(s["apn"] not in ("D", "E") for s in ond), True)
    ok &= check("owner-network signal type", ond[0]["signal_type"], "owner_network_distress")

    # Contagion: 28208 has 3 distressed parcels (A,C,F) -> hot; 28270 has 1 -> not.
    nc, areas = neighborhood_contagion(signals, parcels, hot_threshold=3)
    ok &= check("28208 is hot", areas["28208"]["hot"], True)
    ok &= check("28270 not hot", areas["28270"]["hot"], False)
    nc_apns = sorted({s["apn"] for s in nc})
    ok &= check("contagion flags parcels in hot ZIP", nc_apns, ["A", "B", "C", "F"])
    ok &= check("contagion signal type", nc[0]["signal_type"], "neighborhood_contagion")
    ok &= check("hot area records distress types", "foreclosure" in areas["28208"]["distress_types"], True)

    # resolved signals never count toward network/contagion.
    ok &= check("resolved excluded", all(s["apn"] != "X" for s in ond + nc), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
