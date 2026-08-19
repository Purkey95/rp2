#!/usr/bin/env python3
"""Entity-distress → parcel signals (Secretary of State join).

Secretary-of-State data is keyed by LLC/corp NAME, not by parcel — so a dissolved
entity only becomes an acquisition signal once you join it to the parcels that
entity owns. This reads SoS entity records + the parcel table, matches by
normalized owner name, and emits parcel-keyed signals that flow into the score's
ownership-transition dimension (already weighted).

Bonus: an entity in a distress status that owns MULTIPLE parcels is exactly the
"LLC dissolves → investigate its whole portfolio" case, so those parcels also get
a portfolio_liquidation signal.

Entities in: JSONL with entity_name, status, and (ideally) sos_id, entity_type,
status_date, detail_url. Parcels in: CSV with apn, owner. Pure stdlib.
"""

import argparse
import csv
import json
import re

# SoS status -> our signal_type. Refined from the live status vocabulary; an
# unmapped status yields no signal (an active entity is not a signal).
DEFAULT_STATUS_MAP = {
    "admin. dissolved": "llc_admin_dissolution",
    "administratively dissolved": "llc_admin_dissolution",
    "dissolved": "business_dissolution",
    "voluntarily dissolved": "business_dissolution",
    "revoked": "business_dissolution",
    "suspended": "business_dissolution",
    "withdrawn": "foreign_llc_withdrawal",
    "foreign entity withdrawal": "foreign_llc_withdrawal",
}


def normalize_entity(name):
    """Canonical owner/entity key — keeps LLC/INC (they distinguish legal owners)."""
    if not name:
        return ""
    n = name.lower().strip()
    n = re.sub(r"[.,&/']", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    drop = {"the"}
    return " ".join(sorted(t for t in n.split(" ") if t and t not in drop))


def build_parcel_index(parcels):
    idx = {}
    for p in parcels:
        key = normalize_entity(p.get("owner") or p.get("owner_name") or "")
        if key:
            idx.setdefault(key, []).append(p)
    return idx


def entity_signals(entities, parcels, status_map=None, portfolio_threshold=3):
    status_map = {k.lower(): v for k, v in (status_map or DEFAULT_STATUS_MAP).items()}
    idx = build_parcel_index(parcels)
    signals = []
    stats = {"entities": len(entities), "distressed": 0, "matched_entities": 0,
             "signals": 0, "by_type": {}}

    for e in entities:
        st = (e.get("status") or "").strip().lower()
        signal_type = status_map.get(st)
        if not signal_type:
            continue
        stats["distressed"] += 1
        key = normalize_entity(e.get("entity_name") or e.get("owner") or "")
        owned = idx.get(key, [])
        if not owned:
            continue
        stats["matched_entities"] += 1
        url = e.get("detail_url") or (
            f"https://www.sosnc.gov/online_services/search/Business_Registration_Results"
            f"?SearchCriteria={e.get('sos_id', '')}" if e.get("sos_id") else "")
        for p in owned:
            base = {"apn": p.get("apn", ""), "bucket": "active", "source_url": url,
                    "owner": e.get("entity_name", ""), "source_name": "NC Secretary of State"}
            signals.append({**base, "signal_type": signal_type})
            stats["by_type"][signal_type] = stats["by_type"].get(signal_type, 0) + 1
            stats["signals"] += 1
        if len(owned) >= portfolio_threshold:
            for p in owned:
                signals.append({"apn": p.get("apn", ""), "bucket": "active",
                                "source_url": url, "owner": e.get("entity_name", ""),
                                "source_name": "NC Secretary of State",
                                "signal_type": "portfolio_liquidation"})
                stats["by_type"]["portfolio_liquidation"] = stats["by_type"].get("portfolio_liquidation", 0) + 1
                stats["signals"] += 1
    return signals, stats


def load_entities(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_parcels(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="SoS entity-distress -> parcel signals")
    ap.add_argument("--entities", required=True)
    ap.add_argument("--parcels", required=True)
    ap.add_argument("--out", default="entity_signals.jsonl")
    args = ap.parse_args()
    signals, stats = entity_signals(load_entities(args.entities), load_parcels(args.parcels))
    with open(args.out, "w", encoding="utf-8") as f:
        for s in signals:
            f.write(json.dumps(s) + "\n")
    print(f"Entities: {stats['entities']}  distressed: {stats['distressed']}  "
          f"matched-to-parcels: {stats['matched_entities']}")
    print(f"Signals emitted: {stats['signals']}  by type: {stats['by_type']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
