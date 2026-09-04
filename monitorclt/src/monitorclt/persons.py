"""Person clustering across sources.

A confirmed estate->parcel link creates a person. This module pulls the same person's
other mentions -- deeds they signed, tax rows, foreclosure filings, other parcels --
onto that identity, conservatively: same FIRST|LAST, no middle conflict, and a shared
address or shared parcel. Name alone never merges. The result is a person page that
shows every parcel and record, not one match.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from . import entities
from .normalize.names import name_agreement
from .store import Store, loads

CLUSTER_SOURCES = ("deed", "tax_delinquency", "foreclosure", "code_enforcement", "parcel")


def cluster_persons(store: Store) -> Dict[str, int]:
    counts = {"persons": 0, "attached": 0, "considered": 0}
    for person in store.query("SELECT id FROM person WHERE is_organization = 0"):
        pid = int(person["id"])
        counts["persons"] += 1
        anchors = store.query("SELECT * FROM mention WHERE person_id = ?", (pid,))
        if not anchors:
            continue
        names = [entities.mention_name(a) for a in anchors]
        key_fls = {n.key_fl for n in names}
        addresses: Set[str] = {a["address_norm"] for a in anchors if a["address_norm"]}
        addresses |= {r["address_norm"] for r in store.query("SELECT address_norm FROM person_address WHERE person_id = ?", (pid,))}
        parcels: Set[str] = {a["parcel_id"] for a in anchors if a["parcel_id"]}
        parcels |= {
            r["right_id"]
            for r in store.query("SELECT right_id FROM entity_match WHERE person_id = ? AND status = 'confirmed' AND right_source = 'parcel'", (pid,))
        }
        for key in key_fls:
            rows = store.query(
                "SELECT * FROM mention WHERE key_fl = ? AND current = 1 AND person_id IS NULL AND is_organization = 0 AND source IN ({0})".format(
                    ", ".join("?" for _ in CLUSTER_SOURCES)
                ),
                [key] + list(CLUSTER_SOURCES),
            )
            for row in rows:
                counts["considered"] += 1
                cand = entities.mention_name(row)
                if any(name_agreement(n, cand) == "conflict" for n in names):
                    continue
                shares_address = bool(row["address_norm"] and row["address_norm"] in addresses)
                shares_parcel = bool(row["parcel_id"] and row["parcel_id"] in parcels)
                if not (shares_address or shares_parcel):
                    continue
                entities.attach_mention(store, int(row["id"]), pid)
                store.execute(
                    "INSERT OR IGNORE INTO person_alias (person_id, alias_normalized, source) VALUES (?, ?, ?)", (pid, cand.normalized, row["source"])
                )
                if row["address_norm"]:
                    kind = {"parcel": "tax_mailing", "deed": "situs"}.get(row["source"], "situs")
                    store.execute(
                        "INSERT OR IGNORE INTO person_address (person_id, address_norm, address_kind, observed_at) VALUES (?, ?, ?, ?)",
                        (pid, row["address_norm"], kind, store.now()),
                    )
                    addresses.add(row["address_norm"])
                if row["parcel_id"]:
                    parcels.add(row["parcel_id"])
                counts["attached"] += 1
    store.commit()
    return counts


def profile(store: Store, person_id: int) -> Optional[Dict[str, Any]]:
    p = store.one("SELECT * FROM person WHERE id = ?", (person_id,))
    if p is None:
        return None
    mentions = store.query(
        "SELECT id, source, county, natural_key, role, raw_name, address_norm, parcel_id FROM mention WHERE person_id = ? AND current = 1 ORDER BY source, natural_key",
        (person_id,),
    )
    parcel_ids = sorted(
        {m["parcel_id"] for m in mentions if m["parcel_id"]}
        | {
            r["right_id"]
            for r in store.query("SELECT right_id FROM entity_match WHERE person_id = ? AND status = 'confirmed' AND right_source = 'parcel'", (person_id,))
        }
    )
    parcels = [entities.get_parcel(store, pid) for pid in parcel_ids]
    parcels = [x for x in parcels if x]
    events: List[Dict[str, Any]] = []
    for pid in parcel_ids:
        for e in store.query(
            "SELECT id, kind, occurred_at, observed_at, parcel_id, payload FROM event WHERE parcel_id = ? AND kind NOT LIKE 'record_%' ORDER BY id", (pid,)
        ):
            e["payload"] = loads(e["payload"], {})
            events.append(e)
    return {
        "person": p,
        "aliases": store.query("SELECT alias_normalized, source FROM person_alias WHERE person_id = ? ORDER BY alias_normalized", (person_id,)),
        "addresses": store.query("SELECT address_norm, address_kind, observed_at FROM person_address WHERE person_id = ? ORDER BY address_norm", (person_id,)),
        "mentions": mentions,
        "parcels": parcels,
        "matches": store.query(
            "SELECT id, kind, left_id, right_id, status, probability, decided_by FROM entity_match WHERE person_id = ? ORDER BY id", (person_id,)
        ),
        "events": events,
        "roles": sorted({m["role"] for m in mentions}),
        "sources": sorted({m["source"] for m in mentions}),
    }
