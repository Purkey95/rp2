#!/usr/bin/env python3
"""Seller Opportunity Score — five component scores → one combined lead score.

Instead of 50 lists, maintain five scores per parcel/owner and compose them:

    Financial Distress · Property Distress · Ownership Transition ·
    Landlord Fatigue · Disposition Probability   →   Seller Opportunity Score 0-100

Every signal maps to one dimension (weights.json `dimension_of`). Each dimension
is its own 0-100 component; the combined score is the capped sum of all
contributions, and `dimensions_firing` shows breadth (a lead lit across three
dimensions is more robust than one dimension maxed). Every point traces to its
source. Config-ready signal types score automatically once a source emits them
(see signals_catalog.md), so wiring ROD liens / court records / MLS later needs
no scoring change.

Inputs / outputs / DB wiring: see README.md. Pure stdlib.
"""

import argparse
import csv
import json
import os

from entity import build_portfolios, normalize_owner

HERE = os.path.dirname(os.path.abspath(__file__))


def load_signals(path):
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


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _norm_addr(s):
    return " ".join((s or "").lower().replace(".", " ").replace(",", " ").split())


def derived_signals(parcel, portfolio, current_year):
    """Signals computable from the parcel/owner alone. Returns [(name, why)]."""
    found = []
    mail_st, situs_st = _norm_addr(parcel.get("mail_street")), _norm_addr(parcel.get("situs_street"))
    if mail_st and situs_st and mail_st != situs_st:
        found.append(("absentee", "owner mailing address differs from the property"))
    mstate = (parcel.get("mail_state") or "").strip().upper()
    pstate = (parcel.get("property_state") or parcel.get("situs_state") or "").strip().upper()
    if mstate and pstate and mstate != pstate:
        found.append(("out_of_state", f"owner mails to {mstate}, property in {pstate}"))
    sy = _num(parcel.get("sale_year"))
    if sy and (current_year - int(sy)) >= 20:
        found.append(("long_tenure_20y", f"owned {current_year - int(sy)} years"))
    val = _num(parcel.get("assessed_value") or parcel.get("market_value"))
    mort = _num(parcel.get("mortgage_balance"))
    if val and mort is not None and val > 0 and (val - mort) / val >= 0.70:
        found.append(("high_equity_proxy", f"~{round((val - mort) / val * 100)}% equity"))
    if portfolio:
        if portfolio["size"] >= 5:
            found.append(("large_portfolio", f"owner holds {portfolio['size']} parcels"))
        if portfolio["recent_sale"] and portfolio["size"] >= 2:
            found.append(("recently_sold_another", "owner sold another parcel recently"))
    return found


def score_parcels(signals, parcels, cfg, current_year):
    sig_w, der_w = cfg["signal_weights"], cfg["derived_weights"]
    dim_of, dims = cfg["dimension_of"], list(cfg["dimensions"].keys())
    rel_map, rel_default = cfg.get("reliability", {}), cfg.get("default_reliability", 5)

    by_apn = {}
    for s in signals:
        if s.get("bucket") != "active":
            continue
        apn = (s.get("apn") or "").strip()
        if apn:
            by_apn.setdefault(apn, []).append(s)

    portfolios = build_portfolios(parcels, current_year)

    results = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        pf = portfolios.get(normalize_owner(p.get("owner") or p.get("owner_name") or ""))

        contributors, seen = [], set()
        for s in by_apn.get(apn, []):
            st = s.get("signal_type", "")
            if st in sig_w and st not in seen:
                seen.add(st)
                rel = s.get("reliability", rel_map.get(st, rel_default))
                contributors.append((st, sig_w[st], dim_of.get(st, "financial_distress"),
                                     s.get("source_url", ""), rel))
        for name, why in derived_signals(p, pf, current_year):
            if name in der_w and name not in seen:
                seen.add(name)
                contributors.append((name, der_w[name], dim_of.get(name, "ownership_transition"),
                                     why, rel_map.get(name, rel_default)))

        if not contributors:
            continue

        components = {d: 0 for d in dims}
        for _, pts, dim, _, _ in contributors:
            components[dim] = components.get(dim, 0) + pts
        components = {d: min(v, 100) for d, v in components.items()}
        seller_opportunity = min(sum(pts for _, pts, _, _, _ in contributors), 100)
        firing = sum(1 for v in components.values() if v > 0)
        # Confidence (#15): points-weighted average reliability, 0-100. High score
        # + low confidence = a lead built on soft signals -> visible and filterable.
        pts_total = sum(pts for _, pts, _, _, _ in contributors)
        confidence = round(sum(pts * rel for _, pts, _, _, rel in contributors) / pts_total / 5 * 100) if pts_total else 0

        results.append({
            "apn": apn,
            "owner": p.get("owner") or p.get("owner_name") or "",
            "situs_address": p.get("situs_street") or p.get("situs_address") or "",
            "seller_opportunity_score": seller_opportunity,
            "confidence": confidence,
            "band": _band(seller_opportunity, cfg),
            "components": components,
            "dimensions_firing": firing,
            "portfolio_size": pf["size"] if pf else 1,
            "signals": [
                {"signal": n, "points": pt, "dimension": dim, "reliability": rel, "evidence": src}
                for n, pt, dim, src, rel in sorted(contributors, key=lambda x: -x[1])
            ],
        })

    results.sort(key=lambda r: (-r["seller_opportunity_score"], -r["dimensions_firing"]))
    return results


def _band(score, cfg):
    for b in cfg["bands"]:
        if score >= b["min"]:
            return b["label"]
    return "skip"


def main():
    ap = argparse.ArgumentParser(description="Seller Opportunity Score")
    ap.add_argument("--dsn", help="Postgres DSN: read live signals+parcels, upsert leads")
    ap.add_argument("--signals", help="signals JSONL (file mode)")
    ap.add_argument("--parcels", help="parcels CSV (file mode)")
    ap.add_argument("--weights", default=os.path.join(HERE, "weights.json"))
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    cfg = json.load(open(args.weights, encoding="utf-8"))
    if args.dsn:
        from db import load_signals_db, load_parcels_db, write_leads_db
        signals, parcels = load_signals_db(args.dsn), load_parcels_db(args.dsn)
    else:
        if not (args.signals and args.parcels):
            ap.error("provide --dsn, or both --signals and --parcels")
        signals, parcels = load_signals(args.signals), load_parcels(args.parcels)

    results = score_parcels(signals, parcels, cfg, args.year)

    if args.dsn:
        from db import write_leads_db
        print(f"Upserted {write_leads_db(args.dsn, results)} leads into the database.")

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "scored_leads.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    bands = {}
    for r in results:
        bands[r["band"]] = bands.get(r["band"], 0) + 1
    summary = {"scored_parcels": len(results), "by_band": bands,
               "score.leads_priority_plus": sum(1 for r in results if r["seller_opportunity_score"] >= 61)}
    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"Scored parcels : {len(results)}   by band: {bands}")
    print(f"Priority+ (>=61): {summary['score.leads_priority_plus']}")
    for r in results[:5]:
        print(f"\n{r['situs_address'] or r['apn']}   Seller Opportunity Score: {r['seller_opportunity_score']} "
              f"[{r['band']}]  confidence {r['confidence']}  ({r['dimensions_firing']} dimensions)")
        comps = ", ".join(f"{d.split('_')[0]}:{v}" for d, v in r["components"].items() if v)
        print(f"  components -> {comps}")
        for s in r["signals"]:
            print(f"  +{s['points']:>2} {s['signal']}")
    print(f"\nWrote {args.outdir}/scored_leads.jsonl, summary.json")


if __name__ == "__main__":
    main()
