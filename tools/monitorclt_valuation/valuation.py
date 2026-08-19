#!/usr/bin/env python3
"""Records-based valuation — ARV/AVM + CMA. Turns a lead into an offer.

Everything else in MonitorCLT answers *who to buy from and why now*. Nothing says
*what it's worth* -- and without that you can't make an offer. This is that half.

Same engine a listing agent's CMA tool uses (select comps -> adjustment grid ->
reconcile -> value), but pointed at the acquisition question. Two tiers, one engine:

  Tier 1 (default)  recorded deed sales + assessor characteristics -- free/records
                    data we already hold. An arms-length-sales AVM, no MLS. This is
                    the number that sets your max offer on an off-market lead.
  Tier 2            the SAME code on MLS sold comps (photos/condition) for a
                    retail-grade CMA. Only the comp pool changes.

Method (a transparent appraisal-style adjustment grid, per-comp traceable):
  1. select comps: same type, nearby, recent, within sqft/lot/age bands
  2. adjust each comp TO the subject: time (market drift since sale) + $/sqft,
     beds, baths, lot, age deltas
  3. reconcile: weight by recency + distance + how little adjustment each needed
     (the least-adjusted comp is the best comp), take the weighted mean
  4. offer band: as-is wholesale band, plus the 70%-rule MAO when repairs are given

Pass --today for the time adjustment (determinism). Pure stdlib.
"""

import argparse
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _date(s):
    from datetime import datetime
    return datetime.strptime(str(s)[:10], "%Y-%m-%d")


def _latlon(p):
    return _num(p.get("lat") or p.get("latitude")), _num(p.get("lon") or p.get("longitude"))


def miles_between(a, b):
    """Haversine miles between two parcels, or None if either lacks coordinates."""
    la1, lo1 = _latlon(a)
    la2, lo2 = _latlon(b)
    if None in (la1, lo1, la2, lo2):
        return None
    R = 3958.8
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi = math.radians(la2 - la1)
    dlmb = math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def select_comps(subject, pool, rules, today):
    """Filter the pool to valid comps for the subject."""
    cs = rules["comp_selection"]
    s_sqft = _num(subject.get("sqft") or subject.get("gla"))
    s_lot = _num(subject.get("lot_sqft"))
    s_year = _num(subject.get("year_built"))

    out = []
    for c in pool:
        if (c.get("apn") or "").strip() and (c.get("apn") or "").strip() == (subject.get("apn") or "").strip():
            continue  # never comp a parcel against itself
        if not (_num(c.get("sale_price")) and c.get("sale_date")):
            continue
        # match fields (property type etc.)
        if any((str(subject.get(f, "")).strip().lower() != str(c.get(f, "")).strip().lower())
               for f in cs["match_fields"] if subject.get(f) is not None):
            continue
        age = (today - _date(c["sale_date"])).days
        if age < 0 or age > cs["max_age_days"]:
            continue
        dist = miles_between(subject, c)
        if dist is not None and dist > cs["max_distance_mi"]:
            continue
        c_sqft = _num(c.get("sqft") or c.get("gla"))
        if s_sqft and c_sqft and abs(c_sqft - s_sqft) / s_sqft > cs["sqft_tolerance"]:
            continue
        c_lot = _num(c.get("lot_sqft"))
        if s_lot and c_lot and s_lot > 0 and abs(c_lot - s_lot) / s_lot > cs["lot_tolerance"]:
            continue
        c_year = _num(c.get("year_built"))
        if s_year and c_year and abs(c_year - s_year) > cs["year_built_tolerance"]:
            continue
        out.append({**c, "_age_days": age, "_distance_mi": dist})
    # closest/most-recent first; cap at max_comps (min enforced by caller)
    out.sort(key=lambda c: (c["_distance_mi"] if c["_distance_mi"] is not None else 0, c["_age_days"]))
    return out[: cs["max_comps"]]


def adjust_comp(subject, comp, rules, today):
    """Adjust the comp's sale price to the subject. Returns (adjusted_price, grid)."""
    adj = rules["adjustments"]
    price = _num(comp.get("sale_price"))
    months = (today - _date(comp["sale_date"])).days / 30.0
    time_factor = (1 + adj["monthly_appreciation"]) ** months
    time_adjusted = price * time_factor

    grid = {"sale_price": round(price), "time_adj_months": round(months, 1),
            "time_adjusted": round(time_adjusted)}
    total_feature = 0.0

    def feat(name, s_val, c_val, per_unit):
        nonlocal total_feature
        s, c = _num(s_val), _num(c_val)
        if s is None or c is None:
            return
        delta = (s - c) * per_unit
        if delta:
            grid[name] = round(delta)
            total_feature += delta

    feat("sqft", subject.get("sqft") or subject.get("gla"), comp.get("sqft") or comp.get("gla"), adj["per_sqft"])
    feat("beds", subject.get("beds"), comp.get("beds"), adj["per_bed"])
    feat("baths", subject.get("baths"), comp.get("baths"), adj["per_bath"])
    feat("lot", subject.get("lot_sqft"), comp.get("lot_sqft"), adj["per_lot_sqft"])
    feat("year_built", subject.get("year_built"), comp.get("year_built"), adj["per_year_built"])

    adjusted = time_adjusted + total_feature
    grid["gross_adjustment"] = round(abs(total_feature) + abs(time_adjusted - price))
    grid["adjusted_value"] = round(adjusted)
    return adjusted, grid


def _weight(comp, adjusted, price, rules):
    w = rules["weighting"]
    rec = 0.5 ** (comp["_age_days"] / w["recency_halflife_days"])
    dist = 1.0 if comp["_distance_mi"] is None else 0.5 ** (comp["_distance_mi"] / w["distance_halflife_mi"])
    # smaller gross adjustment relative to price => better comp
    gross_pct = abs(adjusted - price) / price if price else 1.0
    sim = 1.0 / (1.0 + gross_pct)
    return rec * dist * sim


def value_subject(subject, pool, rules, today, repairs=None):
    cs = rules["comp_selection"]
    comps = select_comps(subject, pool, rules, today)
    if len(comps) < cs["min_comps"]:
        return {"apn": (subject.get("apn") or "").strip(), "status": "insufficient_comps",
                "comps_found": len(comps), "min_required": cs["min_comps"]}

    rows, wsum, vsum, adj_prices = [], 0.0, 0.0, []
    for c in comps:
        adjusted, grid = adjust_comp(subject, c, rules, today)
        wt = _weight(c, adjusted, _num(c.get("sale_price")), rules)
        rows.append({"apn": c.get("apn", ""), "distance_mi": None if c["_distance_mi"] is None else round(c["_distance_mi"], 2),
                     "age_days": c["_age_days"], "weight": round(wt, 3), **grid})
        wsum += wt
        vsum += wt * adjusted
        adj_prices.append(adjusted)

    estimate = vsum / wsum if wsum else sum(adj_prices) / len(adj_prices)
    lo, hi = min(adj_prices), max(adj_prices)
    # dispersion-based confidence: tight cluster + more comps + fresher = higher
    spread = (hi - lo) / estimate if estimate else 1.0
    conf = 100 * (1 - min(spread, 1.0)) * min(len(comps) / cs["max_comps"], 1.0)
    conf = round(max(conf, 0))

    off = rules["offer"]
    offer = {"as_is_low": round(estimate * off["wholesale_low"]),
             "as_is_high": round(estimate * off["wholesale_high"])}
    if repairs is not None:
        # treat the reconciled value as resale/ARV for the 70% rule
        offer["mao_70_rule"] = round(estimate * off["arv_rule_factor"] - repairs)
        offer["repairs"] = round(repairs)

    return {
        "apn": (subject.get("apn") or "").strip(),
        "status": "valued",
        "estimated_value": round(estimate),
        "value_range": {"low": round(lo), "high": round(hi)},
        "confidence": conf,
        "comps_used": len(comps),
        "offer_band": offer,
        "grid": sorted(rows, key=lambda r: -r["weight"]),
    }


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
    ap = argparse.ArgumentParser(description="Records-based valuation (ARV/AVM + CMA)")
    ap.add_argument("--subject", required=True, help="subject parcel JSON (file path or inline JSON)")
    ap.add_argument("--comps", required=True, help="candidate comps JSONL (sold, with sale_price/sale_date)")
    ap.add_argument("--rules", default=os.path.join(HERE, "valuation_rules.json"))
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (time adjustment / determinism)")
    ap.add_argument("--repairs", type=float, help="estimated repair budget -> enables 70%%-rule MAO")
    ap.add_argument("--out", default="valuation.json")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    today = _date(args.today)
    subj = json.load(open(args.subject, encoding="utf-8")) if os.path.exists(args.subject) else json.loads(args.subject)

    result = value_subject(subj, load_jsonl(args.comps), rules, today, args.repairs)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    if result["status"] != "valued":
        print(f"APN {result['apn']}: {result['status']} "
              f"({result.get('comps_found')} comps, need {result.get('min_required')})")
        return
    print(f"APN {result['apn']}   Estimated value: ${result['estimated_value']:,}  "
          f"(range ${result['value_range']['low']:,}-${result['value_range']['high']:,}, "
          f"confidence {result['confidence']}, {result['comps_used']} comps)")
    ob = result["offer_band"]
    print(f"  As-is offer band: ${ob['as_is_low']:,} - ${ob['as_is_high']:,}")
    if "mao_70_rule" in ob:
        print(f"  70%-rule MAO (repairs ${ob['repairs']:,}): ${ob['mao_70_rule']:,}")
    print("  Adjustment grid:")
    for r in result["grid"]:
        print(f"    {r['apn'] or '(comp)':<14} sale ${r['sale_price']:,} -> adj ${r['adjusted_value']:,}  "
              f"(w {r['weight']}, {r['age_days']}d)")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
