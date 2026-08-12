"""Pin normalization behavior — the part most likely to silently regress.

Run: python3 test_normalize.py
"""

from normalize import normalize_address, normalize_name, is_absentee


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"[{status}] {label}: {got!r}")
    return got == want


def main():
    ok = True

    # Address: variants of the same street collapse to one key.
    a1 = normalize_address("123 North Main Street", "28202")
    a2 = normalize_address("123 N MAIN ST", "28202")
    ok &= check("addr variant a", a1, "123 n main st 28202")
    ok &= check("addr variant b", a2, "123 n main st 28202")
    ok &= check("addr variants match", a1 == a2, True)

    # Unit designators are stripped so apt numbers don't split a parcel.
    ok &= check("unit stripped",
                normalize_address("456 Oak Ave Apt 3B", "28204"),
                "456 oak ave 28204")

    # Different zips must NOT collide even with the same street.
    ok &= check("zip disambiguates",
                normalize_address("100 Elm St", "28202") !=
                normalize_address("100 Elm St", "28210"),
                True)

    # Empty in, empty out.
    ok &= check("empty addr", normalize_address("", "28202"), "")

    # Name: county "LAST, FIRST" == natural order, honorifics/entity words dropped.
    n1 = normalize_name("SMITH, JOHN A")
    n2 = normalize_name("John A. Smith")
    ok &= check("name county order", n1, "a john smith")
    ok &= check("name natural order", n2, "a john smith")
    ok &= check("names match", n1 == n2, True)
    ok &= check("entity words dropped",
                normalize_name("Carolina Holdings LLC"), "carolina holdings")

    # Absentee: mailing != situs -> True; same -> False; unknown -> None.
    ok &= check("absentee true",
                is_absentee("28202", "123 Main St", "33601", "PO Box 1"), True)
    ok &= check("absentee false",
                is_absentee("28227", "88 Rocky River Rd", "28227", "88 Rocky River Road"), False)
    ok &= check("absentee unknown",
                is_absentee("28202", "123 Main St", "", ""), None)

    print()
    print("ALL PASSED" if ok else "SOME TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
