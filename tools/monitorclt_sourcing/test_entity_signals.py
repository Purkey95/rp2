"""Pin the SoS entity-distress -> parcel join. Run: python3 test_entity_signals.py"""

from entity_signals import entity_signals, normalize_entity


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True
    parcels = [
        {"apn": "A1", "owner": "CAROLINA HOLDINGS LLC"},
        {"apn": "A2", "owner": "Carolina Holdings, LLC"},   # variant, same entity
        {"apn": "A3", "owner": "CAROLINA HOLDINGS LLC"},
        {"apn": "B1", "owner": "PIEDMONT RENTALS INC"},
        {"apn": "C1", "owner": "SMITH JOHN"},               # person, not the entity
    ]
    entities = [
        {"entity_name": "Carolina Holdings LLC", "status": "Admin. Dissolved", "sos_id": "1234567"},
        {"entity_name": "Piedmont Rentals Inc", "status": "Withdrawn", "sos_id": "7654321"},
        {"entity_name": "Active Co LLC", "status": "Current-Active", "sos_id": "9999999"},  # no signal
        {"entity_name": "Ghost Holdings LLC", "status": "Dissolved", "sos_id": "5"},        # owns no parcels
    ]

    signals, stats = entity_signals(entities, parcels, portfolio_threshold=3)

    # name normalization matches the variant.
    ok &= check("name variant normalizes equal",
                normalize_entity("Carolina Holdings, LLC") == normalize_entity("CAROLINA HOLDINGS LLC"), True)

    # admin-dissolved LLC owning 3 parcels -> 3 dissolution signals.
    dis = [s for s in signals if s["signal_type"] == "llc_admin_dissolution"]
    ok &= check("admin-dissolution on all 3 owned parcels", sorted(s["apn"] for s in dis), ["A1", "A2", "A3"])
    # ...and portfolio_liquidation too (>=3 parcels).
    pl = [s for s in signals if s["signal_type"] == "portfolio_liquidation"]
    ok &= check("portfolio_liquidation on the 3-parcel entity", len(pl), 3)

    # withdrawn foreign entity -> foreign_llc_withdrawal on its 1 parcel (no portfolio flag).
    fw = [s for s in signals if s["signal_type"] == "foreign_llc_withdrawal"]
    ok &= check("foreign withdrawal on B1", [s["apn"] for s in fw], ["B1"])

    # active entity -> no signal; entity owning no parcels -> no signal.
    ok &= check("active entity produces nothing", all(s["owner"] != "Active Co LLC" for s in signals), True)
    ok &= check("unowned entity produces nothing", all(s["owner"] != "Ghost Holdings LLC" for s in signals), True)

    # every signal is parcel-keyed with provenance and active bucket.
    ok &= check("signals parcel-keyed + active", all(s["apn"] and s["bucket"] == "active" for s in signals), True)

    ok &= check("stats matched entities", stats["matched_entities"], 2)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
