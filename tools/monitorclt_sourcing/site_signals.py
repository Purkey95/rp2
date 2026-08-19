#!/usr/bin/env python3
"""Address-keyed signals → parcel signals (site→parcel resolver).

Environmental records (NC DEQ brownfields / UST / contaminated sites, EPA FRS)
are keyed by LOCATION, not parcel. The ArcGIS adapter can pull them (they're
ArcGIS layers), but the resulting signals carry an address, not an APN. This
resolver joins those address-keyed signals to parcels by normalized address and
stamps the APN, so they flow into the score's property-distress dimension.

Generic: works for any address-keyed signal source, not just environmental. A
point-in-parcel spatial join (using the layers' lat/lng) is the higher-precision
future upgrade; address match is the dependency-free v1.

Pure stdlib.
"""

import argparse
import csv
import json
import re

_SUFFIX = {"street": "st", "str": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd",
           "drive": "dr", "road": "rd", "lane": "ln", "court": "ct", "place": "pl",
           "circle": "cir", "trail": "trl", "parkway": "pkwy", "highway": "hwy", "terrace": "ter"}
_DIR = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne",
        "northwest": "nw", "southeast": "se", "southwest": "sw"}
_UNIT = {"apt", "unit", "ste", "suite", "#", "bldg", "fl", "lot", "rm"}


def normalize_address(street, zip_code=None):
    if not street:
        return ""
    s = re.sub(r"[.,#]", " ", street.lower())
    s = re.sub(r"\s+", " ", s).strip()
    toks, out, skip = s.split(" "), [], False
    for i, t in enumerate(toks):
        if skip:
            skip = False
            continue
        if t in _UNIT:
            skip = True
            continue
        if t in _DIR and len(toks) > 2:
            out.append(_DIR[t]); continue
        if t in _SUFFIX and i >= len(toks) - 2:
            out.append(_SUFFIX[t]); continue
        out.append(t)
    key = " ".join(out).strip()
    z = (zip_code or "").strip()[:5]
    return f"{key} {z}".strip() if z else key


def resolve_to_parcels(signals, parcels):
    """signals carry situs_address (+optional situs_zip) but no apn. Returns
    apn-stamped signals (matched only) + stats."""
    idx = {}
    for p in parcels:
        key = normalize_address(p.get("situs_address") or p.get("situs_street"), p.get("situs_zip"))
        if key:
            idx.setdefault(key, p)
    out, stats = [], {"input": len(signals), "matched": 0, "unmatched": 0, "by_type": {}}
    for s in signals:
        key = normalize_address(s.get("situs_address") or s.get("situs_street"), s.get("situs_zip"))
        p = idx.get(key)
        if not p:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        st = s.get("signal_type", "")
        stats["by_type"][st] = stats["by_type"].get(st, 0) + 1
        out.append({**s, "apn": p.get("apn", "")})
    return out, stats


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
    ap = argparse.ArgumentParser(description="Resolve address-keyed signals to parcels")
    ap.add_argument("--signals", required=True, help="address-keyed signals JSONL")
    ap.add_argument("--parcels", required=True)
    ap.add_argument("--out", default="site_signals.jsonl")
    args = ap.parse_args()
    signals, stats = resolve_to_parcels(load_jsonl(args.signals), load_parcels(args.parcels))
    with open(args.out, "w", encoding="utf-8") as f:
        for s in signals:
            f.write(json.dumps(s) + "\n")
    print(f"Input {stats['input']}  matched {stats['matched']}  unmatched {stats['unmatched']}")
    print(f"By type: {stats['by_type']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
