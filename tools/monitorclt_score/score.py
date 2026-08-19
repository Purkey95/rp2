#!/usr/bin/env python3
"""Owner Distress Score — turn wired signals into ranked, traceable leads.

Joins the sourcing system's `signals` (provenance-enforced distress evidence) to
the parcel/enrichment layer, computes the derived and behavioral signals we can
compute from owned data, sums configurable weights, adds a stacking bonus for
signals spanning multiple categories (the "stack them" thesis), and emits a
0-100 score per parcel with EVERY contributing point traced back to its source.

Inputs:
  --signals   JSONL of signals (the sourcing adapters' signals.jsonl). Only
              ACTIVE-bucket signals count (resolved = the problem cleared).
  --parcels   CSV of parcels (the enrichment parcels table): apn, owner, situs_*,
              mail_*, property_state, sale_year, assessed_value, mortgage_balance,
              vacant.
  --weights   weights.json (default alongside this file).
  --year      current year (Date is unavailable in this runtime; pass it).

Outputs (in --outdir):
  scored_leads.jsonl   full detail incl. contributing signals + source URLs
  scored_leads.csv     flat ranked table
  summary.json         counts by band + MonitorCLT-style metrics

Pure stdlib. Wire load_* to Postgres on the host (signals table + parcels view).
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


def derived_signals(parcel, portfolio, current_year, cfg):
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
    # High-equity PROXY: only computable when a mortgage/lien figure is present.
    # Parcel layers give value but NOT mortgage balance, so this fires only if the
    # enrichment supplied mortgage_balance (else omitted -- never guessed).
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
    sig_w = cfg["signal_weights"]
    der_w = cfg["derived_weights"]
    cat_of = cfg["category_of"]

    # Index ACTIVE signals by APN (resolved signals = problem cleared, excluded).
    by_apn = {}
    for s in signals:
        if s.get("bucket") != "active":
            continue
        apn = (s.get("apn") or "").strip()
        if apn:
            by_apn.setdefault(apn, []).append(s)

    portfolios = build_portfolios(parcels, current_year)
    owner_pf = {k: v for k, v in portfolios.items()}

    results = []
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        owner_key = normalize_owner(p.get("owner") or p.get("owner_name") or "")
        pf = owner_pf.get(owner_key)

        contributors = []       # (signal, points, category, source_url)
        seen_types = set()

        # 1) sourced signals on this parcel
        for s in by_apn.get(apn, []):
            st = s.get("signal_type", "")
            pts = sig_w.get(st)
            if pts is None or st in seen_types:
                continue
            seen_types.add(st)
            contributors.append((st, pts, cat_of.get(st, "other"), s.get("source_url", "")))

        # 2) derived + behavioral signals
        for name, why in derived_signals(p, pf, current_year, cfg):
            pts = der_w.get(name)
            if pts is None or name in seen_types:
                continue
            seen_types.add(name)
            contributors.append((name, pts, cat_of.get(name, "ownership"), why))

        if not contributors:
            continue

        base = sum(pts for _, pts, _, _ in contributors)
        categories = {c for _, _, c, _ in contributors}
        stack_bonus = min((len(categories) - 1) * cfg["stacking_bonus_per_extra_category"],
                          cfg["stacking_bonus_cap"]) if len(categories) > 1 else 0
        score = min(base + stack_bonus, 100)

        results.append({
            "apn": apn,
            "owner": p.get("owner") or p.get("owner_name") or "",
            "situs_address": p.get("situs_street") or p.get("situs_address") or "",
            "score": score,
            "band": _band(score, cfg),
            "categories": sorted(categories),
            "stacking_bonus": stack_bonus,
            "portfolio_size": pf["size"] if pf else 1,
            "signals": [
                {"signal": n, "points": pt, "category": c, "evidence": src}
                for n, pt, c, src in sorted(contributors, key=lambda x: -x[1])
            ],
        })

    results.sort(key=lambda r: -r["score"])
    return results


def _band(score, cfg):
    for b in cfg["bands"]:
        if score >= b["min"]:
            return b["label"]
    return "skip"


def main():
    ap = argparse.ArgumentParser(description="Owner Distress Score")
    ap.add_argument("--dsn", help="Postgres DSN: read live signals+parcels, upsert leads (host)")
    ap.add_argument("--signals", help="signals JSONL (CSV/file mode; not needed with --dsn)")
    ap.add_argument("--parcels", help="parcels CSV (CSV/file mode; not needed with --dsn)")
    ap.add_argument("--weights", default=os.path.join(HERE, "weights.json"))
    ap.add_argument("--year", type=int, required=True, help="current year (e.g. 2026)")
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
        n = write_leads_db(args.dsn, results)
        print(f"Upserted {n} leads into the database.")

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "scored_leads.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    flat_fields = ["score", "band", "apn", "owner", "situs_address", "portfolio_size",
                   "categories", "stacking_bonus"]
    with open(os.path.join(args.outdir, "scored_leads.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(flat_fields + ["top_signals"])
        for r in results:
            w.writerow([r["score"], r["band"], r["apn"], r["owner"], r["situs_address"],
                        r["portfolio_size"], "|".join(r["categories"]), r["stacking_bonus"],
                        "; ".join(f"{s['signal']}(+{s['points']})" for s in r["signals"])])

    bands = {}
    for r in results:
        bands[r["band"]] = bands.get(r["band"], 0) + 1
    summary = {
        "scored_parcels": len(results),
        "by_band": bands,
        "score.leads_priority_plus": sum(1 for r in results if r["score"] >= 61),
    }
    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"Scored parcels : {len(results)}")
    print(f"By band        : {bands}")
    print(f"Priority+ (>=61): {summary['score.leads_priority_plus']}")
    if results:
        top = results[0]
        print(f"Top lead       : {top['score']} [{top['band']}] {top['owner']} @ {top['situs_address']}")
        print(f"                 signals: {', '.join(s['signal'] for s in top['signals'])}"
              f" (+{top['stacking_bonus']} stack)")
    print(f"Wrote          : {args.outdir}/scored_leads.jsonl, scored_leads.csv, summary.json")


if __name__ == "__main__":
    main()
