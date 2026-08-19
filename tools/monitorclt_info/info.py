#!/usr/bin/env python3
"""Information-arbitrage derivations — records that combine to reveal the invisible.

Information arbitrage is the sixth of the seven: no single record says anything
unusual, but two of them read together reveal value (or a problem) that neither
shows alone. Both of these run on records we already hold.

  tax_lot_legal_lot_mismatch
    One TAX parcel that legally contains several recorded LOTS. The assessor bills
    it as one property, but it can be split and sold as separate legal lots with no
    subdivision process -- hidden inventory the parcel record doesn't advertise.

  address_anomaly_multiunit
    The assessor says one unit, but the parcel carries multiple distinct addresses
    (100 MAIN, 100 MAIN A, 100 MAIN B) or a permit describing a duplex/ADU/multi-
    family. Either hidden income value or an unpermitted-use compliance angle --
    both worth knowing before anyone else does.

Each emits a parcel-keyed signal the five-score consumes, with evidence. These are
inferences from record cross-reference, so the confidence model marks them
lower-reliability -- a lift, not proof. Pure stdlib.
"""

import argparse
import csv
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

MULTIUNIT_KEYWORDS = (
    "duplex", "triplex", "quadplex", "fourplex", "multifamily", "multi-family",
    "multi family", "apartment", "adu", "accessory dwelling", "two family",
    "2-family", "2 family", "second dwelling", "additional dwelling", "units",
)


def _num(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def legal_lot_count(parcel):
    """Explicit legal_lot_count wins; else parse the legal description. Returns int."""
    explicit = _num(parcel.get("legal_lot_count"))
    if explicit is not None:
        return explicit
    desc = (parcel.get("legal_description") or "").upper()
    if not desc:
        return 1
    singles = re.findall(r"\bLOT\s*#?\s*\d+", desc)
    if len(singles) >= 2:
        return len(singles)
    m = re.search(r"\bLOTS\b(.*?)(?:\bBLOCK\b|\bSEC\b|\bSUBDIVISION\b|\bPLAT\b|\bMAP\b|$)", desc)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        return max(len(nums), 2)  # plural "LOTS" implies at least 2
    return 1


def tax_lot_legal_lot_mismatch(parcels, min_lots=2):
    out = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        if not apn:
            continue
        n = legal_lot_count(p)
        if n < min_lots:
            continue
        out.append({
            "apn": apn, "signal_type": "tax_lot_legal_lot_mismatch", "bucket": "active",
            "source_name": "MonitorCLT info: tax-lot/legal-lot (derived)",
            "source_url": f"one tax parcel contains {n} recorded legal lots -- splittable without subdivision",
            "legal_lots": n,
        })
    return out


def _norm_addr(s):
    return " ".join((s or "").lower().replace(".", " ").replace(",", " ").split())


def address_anomaly_multiunit(parcels, addresses, permits):
    """assessor_units vs distinct addresses / multifamily permit language."""
    addr_by_apn = {}
    for a in addresses:
        apn = (a.get("apn") or "").strip()
        na = _norm_addr(a.get("address") or a.get("full_address") or "")
        if apn and na:
            addr_by_apn.setdefault(apn, set()).add(na)

    permit_hint = {}
    for pr in permits:
        apn = (pr.get("apn") or "").strip()
        if not apn:
            continue
        text = " ".join(str(pr.get(k, "")) for k in ("permit_type", "description", "work_type")).lower()
        if any(k in text for k in MULTIUNIT_KEYWORDS):
            permit_hint[apn] = text.strip()

    out = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        if not apn:
            continue
        assessor = _num(p.get("assessor_units") or p.get("units")) or 1
        distinct = len(addr_by_apn.get(apn, set()))
        hint = permit_hint.get(apn)
        implied = max(distinct, 2 if hint else 0)
        if implied <= assessor:
            continue
        why = f"assessor says {assessor} unit(s); "
        why += (f"{distinct} distinct addresses on parcel" if distinct > assessor
                else f"permit indicates multi-unit ({hint[:40]})")
        out.append({
            "apn": apn, "signal_type": "address_anomaly_multiunit", "bucket": "active",
            "source_name": "MonitorCLT info: address/permit anomaly (derived)",
            "source_url": why, "assessor_units": assessor, "implied_units": implied,
        })
    return out


def load_rows(path):
    out = []
    if not path:
        return out
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="Information-arbitrage derivations")
    ap.add_argument("--parcels", required=True, help="parcels CSV/JSONL (apn, legal_lot_count/legal_description, assessor_units)")
    ap.add_argument("--addresses", help="addresses JSONL/CSV (apn, address)")
    ap.add_argument("--permits", help="permits JSONL/CSV (apn, permit_type/description)")
    ap.add_argument("--out", default="info_signals.jsonl")
    args = ap.parse_args()

    parcels = load_rows(args.parcels)
    sigs = tax_lot_legal_lot_mismatch(parcels)
    sigs += address_anomaly_multiunit(parcels, load_rows(args.addresses), load_rows(args.permits))

    with open(args.out, "w", encoding="utf-8") as f:
        for s in sigs:
            f.write(json.dumps(s) + "\n")

    counts = {}
    for s in sigs:
        counts[s["signal_type"]] = counts.get(s["signal_type"], 0) + 1
    print("Information signals:")
    for k, v in sorted(counts.items()):
        print(f"  {v:>4}  {k}")
    for s in sigs[:8]:
        print(f"  APN {s['apn']:<14} {s['signal_type']:<28} {s['source_url']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
