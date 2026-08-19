#!/usr/bin/env python3
"""Parcel-geometry wedges — Relationship and Development arbitrage from the parcel layer alone.

The taxonomy's coverage-by-arbitrage view shows two classes at 0 built:
*relationship* and *development*. Those are the most differentiated, hardest-to-
copy arbitrages -- and the one slice of each that needs NO external data, just the
parcel layer we already hold (adjacency, road frontage, zoning, lot area, units):

  relationship_value_access
    "Who would this parcel be worth substantially more to?" A parcel with no road
    frontage whose only access crosses a specific neighbor is worth far more to
    that neighbor than to the open market -- a natural buyer already exists, so a
    deal is unusually doable. That's Relationship arbitrage in its purest,
    computable form (the landlocked / controlled-access case).

  hidden_density_zoning_mismatch
    "What is this property capable of becoming?" Zoning-implied max units minus
    what's actually built. A parcel zoned for 22 units with a single house on it is
    redevelopment upside the parcel record doesn't advertise -- Development arbitrage.

Both emit parcel-keyed signals (disposition dimension) with evidence, and both are
derived inferences from geometry/zoning fields, so the confidence model marks them
lower-reliability: a lift, not hard evidence. Pure stdlib.
"""

import argparse
import csv
import json
import os


def _truthy(v, true_values):
    return str(v).strip().lower() in true_values


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _neighbors(parcel):
    n = parcel.get("neighbors")
    if isinstance(n, list):
        return [str(x).strip() for x in n if str(x).strip()]
    if isinstance(n, str):
        return [x.strip() for x in n.replace(";", ",").split(",") if x.strip()]
    return []


def relationship_value_access(parcels, rules):
    """A landlocked parcel (no road frontage) whose neighbors include frontage
    parcels: those neighbors control its access, so it's worth disproportionately
    more to one of them. Emit on the landlocked parcel (our motivated seller),
    naming the controlling neighbor(s)."""
    cfg = rules["relationship_value_access"]
    tv = set(cfg["frontage_true_values"])
    index = {(p.get("apn") or "").strip(): p for p in parcels if (p.get("apn") or "").strip()}

    out = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        if not apn:
            continue
        if _truthy(p.get("road_frontage", ""), tv):
            continue  # has its own access -> not landlocked
        controllers = []
        for nb in _neighbors(p):
            npar = index.get(nb)
            if npar is not None and _truthy(npar.get("road_frontage", ""), tv):
                controllers.append(nb)
        if not controllers:
            continue
        sole = len(controllers) == 1
        out.append({
            "apn": apn, "signal_type": "relationship_value_access", "bucket": "active",
            "source_name": "MonitorCLT geometry: controlled access (derived)",
            "source_url": f"landlocked; access controlled by neighbor(s) {', '.join(controllers)}"
                          + ("  [sole controller -- strongest leverage]" if sole else ""),
            "controllers": controllers, "sole_controller": sole,
        })
    return out


def zoning_max_units(parcel, cfg):
    """Explicit zoning_max_units wins; else lot_area_acres x units_per_acre[zoning]."""
    explicit = _num(parcel.get("zoning_max_units"))
    if explicit is not None:
        return explicit
    zoning = (parcel.get("zoning") or "").strip().upper()
    per_acre = cfg["units_per_acre"].get(zoning)
    acres = _num(parcel.get("lot_area_acres"))
    if acres is None:
        sqft = _num(parcel.get("lot_area_sqft"))
        acres = sqft / 43560 if sqft is not None else None
    if per_acre is not None and acres is not None:
        return per_acre * acres
    return None


def hidden_density_zoning_mismatch(parcels, rules):
    """Emit when zoning-implied max units exceeds existing units by >= min_extra_units."""
    cfg = rules["hidden_density"]
    out = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        if not apn:
            continue
        allowed = zoning_max_units(p, cfg)
        existing = _num(p.get("existing_units"))
        if existing is None:
            existing = _num(p.get("units")) or 1  # a built parcel has at least 1
        if allowed is None:
            continue
        extra = int(allowed) - int(existing)
        if extra < cfg["min_extra_units"]:
            continue
        out.append({
            "apn": apn, "signal_type": "hidden_density_zoning_mismatch", "bucket": "active",
            "source_name": "MonitorCLT geometry: hidden density (derived)",
            "source_url": f"zoned ~{int(allowed)} units ({(p.get('zoning') or '?').strip()}), "
                          f"{int(existing)} present -> ~{extra} unit upside",
            "extra_units": extra,
        })
    return out


def load_parcels(path):
    if path.endswith(".jsonl"):
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="Parcel-geometry wedges (relationship + development)")
    ap.add_argument("--parcels", required=True, help="parcels CSV or JSONL (apn, road_frontage, neighbors, zoning, lot_area_acres, existing_units)")
    ap.add_argument("--rules", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "geometry_rules.json"))
    ap.add_argument("--out", default="geometry_signals.jsonl")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    parcels = load_parcels(args.parcels)

    sigs = relationship_value_access(parcels, rules) + hidden_density_zoning_mismatch(parcels, rules)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sigs:
            f.write(json.dumps(s) + "\n")

    counts = {}
    for s in sigs:
        counts[s["signal_type"]] = counts.get(s["signal_type"], 0) + 1
    print("Geometry signals:")
    for k, v in sorted(counts.items()):
        print(f"  {v:>4}  {k}")
    for s in sigs[:8]:
        print(f"  APN {s['apn']:<14} {s['signal_type']:<32} {s['source_url']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
