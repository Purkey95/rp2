"""Pin the site->parcel address resolver. Run: python3 test_site_signals.py"""

from site_signals import resolve_to_parcels, normalize_address


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True
    ok &= check("addr variants normalize equal",
                normalize_address("2444 Wilkinson Blvd", "28208") ==
                normalize_address("2444 WILKINSON BOULEVARD", "28208"), True)

    parcels = [
        {"apn": "05512304", "situs_address": "2444 Wilkinson Blvd", "situs_zip": "28208"},
        {"apn": "07104521", "situs_address": "2000 South Boulevard", "situs_zip": "28203"},
    ]
    # env signals arrive with an address (from NC DEQ), no APN.
    env = [
        {"situs_address": "2444 WILKINSON BOULEVARD", "situs_zip": "28208",
         "signal_type": "ust_issue", "source_url": "https://deq.example/ust/1", "source_name": "NC DEQ UST"},
        {"situs_address": "2000 South Blvd", "situs_zip": "28203",
         "signal_type": "brownfield", "source_url": "https://deq.example/bf/1", "source_name": "NC DEQ Brownfields"},
        {"situs_address": "9999 Nowhere Rd", "situs_zip": "00000",
         "signal_type": "contaminated_site", "source_url": "https://deq.example/ihs/1", "source_name": "NC DEQ IHS"},
    ]
    resolved, stats = resolve_to_parcels(env, parcels)

    ok &= check("matched two of three", stats["matched"], 2)
    ok &= check("one unmatched (no parcel)", stats["unmatched"], 1)
    # each matched signal is now apn-keyed.
    by_apn = {s["apn"]: s for s in resolved}
    ok &= check("ust stamped onto 05512304", by_apn["05512304"]["signal_type"], "ust_issue")
    ok &= check("brownfield stamped onto 07104521", by_apn["07104521"]["signal_type"], "brownfield")
    ok &= check("provenance preserved", by_apn["05512304"]["source_url"].startswith("http"), True)
    ok &= check("all resolved carry apn", all(s["apn"] for s in resolved), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
