#!/usr/bin/env python3
"""Network intelligence — owner-network distress + neighborhood contagion.

Two derivations, both computed from data we already have (active signals + the
parcel table). They don't look at a property in isolation; they look at the
OWNER's whole portfolio and the BLOCK around it.

  owner_network_distress
    "If property A goes distressed, maybe nothing. If A, C and F all go distressed
    at once, owner-level distress probability jumps." Groups parcels by owner;
    when >=2 of an owner's parcels carry active distress, every parcel in that
    portfolio gets an owner_network_distress signal (disposition dimension).

  neighborhood_contagion
    Score micro-markets, not just parcels. Aggregates active distress signals by
    area (ZIP for v1); parcels in a hot area get a neighborhood_contagion signal
    (property dimension). A rising cluster of code/eviction/tax/vacancy in a small
    area flags a turning block before most investors notice.

Both emit parcel-keyed signals that flow into the score. Pure stdlib.
(Refinements: radius/geohash instead of ZIP, and time-trend — both need lat/lng
and dated events, which the timeline engine already models.)
"""

import argparse
import csv
import json
import re

# Signal types that count as "distress" for network/contagion aggregation.
DISTRESS = {
    "tax_delinquency", "tax_sale", "foreclosure", "lis_pendens", "notice_of_default",
    "code_violation", "nuisance", "housing_violation", "unsafe_structure", "vacancy",
    "demolition_permit", "failed_inspection", "eviction", "repeat_eviction",
    "mechanics_lien", "hoa_lien", "municipal_lien", "boarding_order",
}


def _norm_owner(name):
    if not name:
        return ""
    n = re.sub(r"[.,&/']", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return " ".join(sorted(t for t in n.split(" ") if t and t != "the"))


def _active_distress_by_apn(signals):
    out = {}
    for s in signals:
        if s.get("bucket") == "active" and s.get("signal_type") in DISTRESS:
            apn = (s.get("apn") or "").strip()
            if apn:
                out.setdefault(apn, set()).add(s["signal_type"])
    return out


def owner_network_distress(signals, parcels, min_distressed=2):
    """Emit owner_network_distress on every parcel of an owner with >=min_distressed
    distressed parcels."""
    distress = _active_distress_by_apn(signals)
    by_owner = {}
    for p in parcels:
        by_owner.setdefault(_norm_owner(p.get("owner") or p.get("owner_name") or ""), []).append(p)
    out = []
    for owner, plist in by_owner.items():
        if not owner:
            continue
        distressed = [p for p in plist if (p.get("apn") or "").strip() in distress]
        if len(distressed) >= min_distressed:
            for p in plist:
                out.append({"apn": p.get("apn", ""), "signal_type": "owner_network_distress",
                            "bucket": "active", "source_name": "MonitorCLT owner-network",
                            "source_url": f"portfolio: {len(distressed)}/{len(plist)} parcels distressed",
                            "owner": p.get("owner", "")})
    return out


def neighborhood_contagion(signals, parcels, area_field="situs_zip", hot_threshold=3):
    """Aggregate active distress by area; flag parcels in hot areas. Returns
    (signals, area_scores)."""
    distress = _active_distress_by_apn(signals)
    apn_area = {}
    area_stats = {}
    for p in parcels:
        area = (p.get(area_field) or "").strip()
        apn = (p.get("apn") or "").strip()
        if not area or not apn:
            continue
        apn_area[apn] = area
        area_stats.setdefault(area, {"parcels": 0, "distressed": 0, "signal_count": 0, "types": set()})
        area_stats[area]["parcels"] += 1
        if apn in distress:
            area_stats[area]["distressed"] += 1
            area_stats[area]["signal_count"] += len(distress[apn])
            area_stats[area]["types"] |= distress[apn]

    area_scores = {}
    for area, st in area_stats.items():
        # contagion = distressed density x variety of distress types
        density = st["distressed"] / st["parcels"] if st["parcels"] else 0
        score = round(density * 100 + len(st["types"]) * 5, 1)
        area_scores[area] = {"score": score, "distressed": st["distressed"],
                             "parcels": st["parcels"], "distress_types": sorted(st["types"]),
                             "hot": st["distressed"] >= hot_threshold}

    out = []
    for apn, area in apn_area.items():
        if area_scores.get(area, {}).get("hot"):
            out.append({"apn": apn, "signal_type": "neighborhood_contagion", "bucket": "active",
                        "source_name": "MonitorCLT neighborhood-contagion",
                        "source_url": f"area {area}: {area_scores[area]['distressed']}/"
                                      f"{area_scores[area]['parcels']} distressed",
                        "situs_zip": area})
    return out, area_scores


def load_jsonl(path):
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
    ap = argparse.ArgumentParser(description="Owner-network + neighborhood-contagion signals")
    ap.add_argument("--signals", required=True)
    ap.add_argument("--parcels", required=True)
    ap.add_argument("--out", default="network_signals.jsonl")
    args = ap.parse_args()
    signals, parcels = load_jsonl(args.signals), load_parcels(args.parcels)

    ond = owner_network_distress(signals, parcels)
    nc, areas = neighborhood_contagion(signals, parcels)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in ond + nc:
            f.write(json.dumps(s) + "\n")

    print(f"owner_network_distress signals: {len(ond)}")
    print(f"neighborhood_contagion signals: {len(nc)}")
    hot = {a: v for a, v in areas.items() if v["hot"]}
    print(f"hot areas: {len(hot)}")
    for a, v in sorted(hot.items(), key=lambda kv: -kv[1]["score"])[:5]:
        print(f"  {a}: contagion {v['score']}  ({v['distressed']}/{v['parcels']} distressed, "
              f"types: {', '.join(v['distress_types'])})")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
