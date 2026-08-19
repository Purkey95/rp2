#!/usr/bin/env python3
"""Derived-history signals — stronger signals from the record history we already have.

The cheapest, highest-leverage builds in the whole taxonomy: no new source, just
reading the HISTORY of records we already ingest and deriving a signal that's worth
far more than any single row.

  tax_delinquency_multiyear   One delinquent year is a signal; N consecutive years
                              with a rising balance is a story -- and in NC, ~2yr
                              of delinquency is foreclosure-eligible. This is TIMING
                              arbitrage: the trajectory, not the snapshot.

  complaint_velocity_311      One 311 complaint is noise; a rising count in a
                              trailing window (5 this year, up from 1) is a property
                              visibly deteriorating before a formal code case opens.

  stalled_subdivision         A recorded plat with effectively no vertical (building)
                              permits since = a subdivision that stalled after paper
                              approval. Negative-space: the expected follow-up (homes
                              going up) never happened.

Each emits a parcel-keyed signal the five-score consumes like any other, with
evidence describing the derivation. Pass --today for determinism. Pure stdlib.
"""

import argparse
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def tax_delinquency_multiyear(tax_history, rules, current_year):
    """tax_history rows: {apn, year, delinquent(bool-ish), balance?}. Emit when a
    parcel is delinquent for >= min_consecutive_years consecutive years ending at
    (or one short of) the current year."""
    cfg = rules["tax_staging"]
    by_apn = {}
    for r in tax_history:
        apn = (r.get("apn") or "").strip()
        if not apn:
            continue
        yr = r.get("year")
        try:
            yr = int(yr)
        except (TypeError, ValueError):
            continue
        de = str(r.get("delinquent", "")).strip().lower() in ("1", "true", "yes", "y", "delinquent")
        by_apn.setdefault(apn, {})[yr] = {"delinquent": de, "balance": _num(r.get("balance"))}

    out = []
    for apn, years in by_apn.items():
        # longest run of consecutive delinquent years ending at the most recent
        # delinquent year (staging is about a run right up to now).
        delinquent_years = sorted(y for y, v in years.items() if v["delinquent"])
        if not delinquent_years:
            continue
        # walk back from the latest delinquent year while the prior year is also delinquent
        latest = delinquent_years[-1]
        run, y = 0, latest
        while y in years and years[y]["delinquent"]:
            run += 1
            y -= 1
        if run < cfg["min_consecutive_years"]:
            continue
        first_yr = latest - run + 1
        b0 = years.get(first_yr, {}).get("balance")
        b1 = years.get(latest, {}).get("balance")
        rising = (b0 is not None and b1 is not None and b1 > b0)
        detail = f"{run} consecutive delinquent years ({first_yr}-{latest})"
        if rising:
            detail += f", balance ${b0:,.0f} -> ${b1:,.0f} (rising)"
        hot = run >= cfg["hot_consecutive_years"]
        out.append({"apn": apn, "signal_type": "tax_delinquency_multiyear", "bucket": "active",
                    "source_name": "MonitorCLT tax staging (derived)",
                    "source_url": detail + ("  [foreclosure-ripe]" if hot else ""),
                    "consecutive_years": run, "rising_balance": rising})
    return out


def complaint_velocity_311(complaints, rules, today):
    """complaints rows: {apn, date, type?}. Emit when trailing-window count meets
    min_recent AND is rising vs the prior equal window (acceleration)."""
    cfg = rules["complaint_velocity"]
    w = cfg["window_days"]
    by_apn = {}
    for c in complaints:
        apn = (c.get("apn") or "").strip()
        if apn and c.get("date"):
            by_apn.setdefault(apn, []).append(_date(c["date"]))

    out = []
    for apn, dates in by_apn.items():
        recent = sum(1 for d in dates if 0 <= (today - d).days <= w)
        prior = sum(1 for d in dates if w < (today - d).days <= 2 * w)
        if recent < cfg["min_recent"]:
            continue
        rising = recent >= max(prior * cfg["rising_ratio"], cfg["min_recent"])
        if not rising:
            continue
        out.append({"apn": apn, "signal_type": "complaint_velocity_311", "bucket": "active",
                    "source_name": "MonitorCLT 311 velocity (derived)",
                    "source_url": f"{recent} complaints in last {w}d (prior {w}d: {prior}) -- rising",
                    "recent": recent, "prior": prior})
    return out


def stalled_subdivision(plats, permits, rules, today):
    """plats rows: {apn, plat_date, lots?}. permits rows: {apn, permit_type?, date}.
    Emit when a plat is older than min_plat_age_days and vertical permits since the
    plat are <= max_vertical_permits (the homes never came)."""
    cfg = rules["stalled_subdivision"]
    verticals = {"building", "new_residential", "single_family", "residential_new",
                 "new_construction", "sfr", "vertical"}
    permits_by_apn = {}
    for p in permits:
        apn = (p.get("apn") or "").strip()
        if apn and p.get("date"):
            permits_by_apn.setdefault(apn, []).append(p)

    out = []
    for pl in plats:
        apn = (pl.get("apn") or "").strip()
        if not apn or not pl.get("plat_date"):
            continue
        plat_dt = _date(pl["plat_date"])
        if (today - plat_dt).days < cfg["min_plat_age_days"]:
            continue
        vcount = sum(1 for p in permits_by_apn.get(apn, [])
                     if _date(p["date"]) >= plat_dt
                     and (p.get("permit_type", "").strip().lower() in verticals))
        if vcount > cfg["max_vertical_permits"]:
            continue
        age_mo = (today - plat_dt).days // 30
        out.append({"apn": apn, "signal_type": "stalled_subdivision", "bucket": "active",
                    "source_name": "MonitorCLT stalled-subdivision (derived, negative-space)",
                    "source_url": f"plat recorded {pl['plat_date'][:10]} ({age_mo}mo ago), "
                                  f"{vcount} vertical permits since",
                    "lots": pl.get("lots")})
    return out


def load_jsonl(path):
    out = []
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="Derived-history signals")
    ap.add_argument("--tax-history", help="tax history JSONL (apn, year, delinquent, balance)")
    ap.add_argument("--complaints", help="311 complaints JSONL (apn, date)")
    ap.add_argument("--plats", help="recorded plats JSONL (apn, plat_date, lots)")
    ap.add_argument("--permits", help="permits JSONL (apn, permit_type, date)")
    ap.add_argument("--rules", default=os.path.join(HERE, "derive_rules.json"))
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (determinism)")
    ap.add_argument("--year", type=int, help="current tax year (defaults to --today's year)")
    ap.add_argument("--out", default="derived_signals.jsonl")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    today = _date(args.today)
    year = args.year or today.year

    sigs = []
    sigs += tax_delinquency_multiyear(load_jsonl(args.tax_history), rules, year)
    sigs += complaint_velocity_311(load_jsonl(args.complaints), rules, today)
    sigs += stalled_subdivision(load_jsonl(args.plats), load_jsonl(args.permits), rules, today)

    with open(args.out, "w", encoding="utf-8") as f:
        for s in sigs:
            f.write(json.dumps(s) + "\n")

    counts = {}
    for s in sigs:
        counts[s["signal_type"]] = counts.get(s["signal_type"], 0) + 1
    print("Derived signals:")
    for k, v in sorted(counts.items()):
        print(f"  {v:>4}  {k}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
