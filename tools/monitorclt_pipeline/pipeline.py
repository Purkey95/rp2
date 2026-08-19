#!/usr/bin/env python3
"""MonitorCLT end-to-end pipeline — raw signals in, ranked offer sheet out.

The modules each do one job; this runs them as one flow so the whole engine is a
single command:

    raw signals
       -> AUGMENT   derive-history + geometry + info + network + catalyst signals
       -> LIFECYCLE drop resolved/expired leads (keep only live)
       -> SCORE     five-component Seller Opportunity Score + confidence
       -> WHY-NOW   temporal score from dated events (optional)
       -> VALUE     records AVM + offer band on each marketable lead
       -> OFFER SHEET  ranked: who to pursue, why, and what to offer

Every step is an existing, tested module; this file only orchestrates. `run()`
takes already-loaded python objects (so it's unit-testable without files); `main()`
wires files/CLI around it. Pure stdlib. Pass --today/--year for determinism.
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)

# Each module lives in its own directory; put them on the path and import as
# namespaces (they define same-named helpers like load_jsonl/_date, so we never
# import their functions into a shared namespace).
for _d in ("monitorclt_derive", "monitorclt_geometry", "monitorclt_info",
           "monitorclt_network", "monitorclt_catalyst", "monitorclt_lifecycle",
           "monitorclt_score", "monitorclt_valuation", "monitorclt_timeline",
           "monitorclt_market"):
    p = os.path.join(TOOLS, _d)
    if p not in sys.path:
        sys.path.insert(0, p)

import derive          # noqa: E402
import geometry        # noqa: E402
import info            # noqa: E402
import network         # noqa: E402
import catalyst        # noqa: E402
import lifecycle       # noqa: E402
import score as score_mod   # noqa: E402
import valuation       # noqa: E402
import timeline        # noqa: E402
import market          # noqa: E402


def load_default_configs():
    def rd(d, name):
        return json.load(open(os.path.join(TOOLS, d, name), encoding="utf-8"))
    return {
        "weights": rd("monitorclt_score", "weights.json"),
        "lifecycle": rd("monitorclt_lifecycle", "lifecycle_rules.json"),
        "derive": rd("monitorclt_derive", "derive_rules.json"),
        "geometry": rd("monitorclt_geometry", "geometry_rules.json"),
        "catalyst": rd("monitorclt_catalyst", "catalyst_rules.json"),
        "valuation": rd("monitorclt_valuation", "valuation_rules.json"),
        "timeline": rd("monitorclt_timeline", "rules.json"),
    }


def augment_signals(raw_signals, parcels, today, year, inputs, cfgs):
    """Add every derived signal we can compute to the raw stream."""
    sigs = list(raw_signals)
    counts = {}

    def add(new, label):
        counts[label] = counts.get(label, 0) + len(new)
        sigs.extend(new)

    if inputs.get("tax_history"):
        add(derive.tax_delinquency_multiyear(inputs["tax_history"], cfgs["derive"], year), "derive")
    if inputs.get("complaints"):
        add(derive.complaint_velocity_311(inputs["complaints"], cfgs["derive"], today), "derive")
    if inputs.get("plats"):
        add(derive.stalled_subdivision(inputs["plats"], inputs.get("permits") or [], cfgs["derive"], today), "derive")

    add(geometry.relationship_value_access(parcels, cfgs["geometry"]), "geometry")
    add(geometry.hidden_density_zoning_mismatch(parcels, cfgs["geometry"]), "geometry")
    add(geometry.assemblage_adjacency_value(parcels, cfgs["geometry"]), "geometry")

    add(info.tax_lot_legal_lot_mismatch(parcels), "info")
    add(info.address_anomaly_multiunit(parcels, inputs.get("addresses") or [], inputs.get("permits") or []), "info")

    # network reads the distress signals accumulated so far
    add(network.owner_network_distress(sigs, parcels), "network")
    nc, _areas = network.neighborhood_contagion(sigs, parcels)
    add(nc, "network")

    if inputs.get("catalyst_events"):
        cat = catalyst.score_parcels(inputs["catalyst_events"], cfgs["catalyst"], today)
        add(catalyst.to_signals(cat), "catalyst")

    return sigs, counts


def _why_now_by_apn(raw_signals, cfgs, today):
    """Temporal why-now from dated events (only signals carrying a date)."""
    events = [s for s in raw_signals if s.get("date")]
    if not events:
        return {}
    sev = {}
    sev.update(cfgs["weights"].get("signal_weights", {}))
    sev.update(cfgs["weights"].get("derived_weights", {}))
    out = {}
    for apn, evs in timeline.by_parcel(events).items():
        r = timeline.score_timeline(evs, cfgs["timeline"], sev, today)
        if r:
            out[apn] = r["why_now"]
    return out


def apply_market_overlay(rows, market_report, exit_haircut=0.95):
    """Macro WHEN/WHERE gate. Attaches the market posture and, when the exit is
    soft (>=2 exit cautions), a CONSERVATIVE market-adjusted offer high -- the
    original band is untouched, and the parcel SCORE is never modulated. Returns
    the posture (or None)."""
    if not market_report:
        return None
    posture = market.timing_posture(market_report)
    soft_exit = posture["exit_cautions"] >= 2
    for r in rows:
        r["market_stance"] = posture["stance"]
        v = r.get("valuation")
        if soft_exit and v and v.get("status") == "valued":
            ob = v["offer_band"]
            ob["market_adjusted_high"] = round(ob["as_is_high"] * exit_haircut)
            ob["market_note"] = (f"exit-caution haircut {round((1 - exit_haircut) * 100)}% "
                                 f"applied ({posture['stance']})")
    return posture


def run(raw_signals, parcels, comps, today, year, cfgs, inputs=None,
        value_threshold=21, repairs_by_apn=None, market_report=None):
    """Orchestrate the full flow, returning ranked offer-sheet rows."""
    inputs = inputs or {}
    repairs_by_apn = repairs_by_apn or {}

    augmented, aug_counts = augment_signals(raw_signals, parcels, today, year, inputs, cfgs)
    live, dropped = lifecycle.apply(augmented, cfgs["lifecycle"], today)
    leads = score_mod.score_parcels(live, parcels, cfgs["weights"], year)
    why_now = _why_now_by_apn(raw_signals, cfgs, today)
    parcel_by_apn = {(p.get("apn") or "").strip(): p for p in parcels}

    rows = []
    for lead in leads:
        apn = lead["apn"]
        row = {
            "apn": apn,
            "owner": lead.get("owner", ""),
            "situs_address": lead.get("situs_address", ""),
            "seller_opportunity_score": lead["seller_opportunity_score"],
            "band": lead["band"],
            "confidence": lead["confidence"],
            "dimensions_firing": lead["dimensions_firing"],
            "why_now": why_now.get(apn),
            "top_signals": [s["signal"] for s in lead["signals"][:5]],
            "valuation": None,
        }
        if lead["seller_opportunity_score"] >= value_threshold and comps:
            subj = parcel_by_apn.get(apn)
            if subj is not None:
                val = valuation.value_subject(subj, comps, cfgs["valuation"], today,
                                              repairs=repairs_by_apn.get(apn))
                row["valuation"] = val
        rows.append(row)

    posture = apply_market_overlay(rows, market_report)

    return {"offer_sheet": rows, "augment_counts": aug_counts,
            "signals_live": len(live), "signals_dropped": len(dropped),
            "leads": len(leads), "market_context": posture}


# ---- file/CLI plumbing ----

def _load_jsonl(path):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_parcels(path):
    if not path:
        return []
    if path.endswith(".jsonl"):
        return _load_jsonl(path)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="MonitorCLT end-to-end pipeline -> offer sheet")
    ap.add_argument("--signals", required=True, help="raw signals JSONL")
    ap.add_argument("--parcels", required=True, help="parcels CSV/JSONL (characteristics + geometry + valuation fields)")
    ap.add_argument("--comps", help="sold comps JSONL (for valuation)")
    ap.add_argument("--tax-history", help="tax history JSONL")
    ap.add_argument("--complaints", help="311 complaints JSONL")
    ap.add_argument("--plats", help="recorded plats JSONL")
    ap.add_argument("--permits", help="permits JSONL")
    ap.add_argument("--addresses", help="addresses JSONL")
    ap.add_argument("--catalyst-events", help="dated catalyst events JSONL")
    ap.add_argument("--today", required=True, help="YYYY-MM-DD")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--value-threshold", type=int, default=21, help="min score to run a valuation")
    ap.add_argument("--market-offline", help="dir of cached FRED <id>.csv -> build the market posture gate")
    ap.add_argument("--dsn", help="Postgres DSN -> upsert the offer sheet into the leads table")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    today = timeline._date(args.today)
    cfgs = load_default_configs()

    market_report = None
    if args.market_offline:
        series_cfg = json.load(open(os.path.join(TOOLS, "monitorclt_market", "series.json"), encoding="utf-8"))

        def _fetch(url):
            sid = url.split("id=")[1].split("&")[0]
            return open(os.path.join(args.market_offline, f"{sid}.csv"), encoding="utf-8").read()
        market_report = market.build_report(series_cfg, fetch=_fetch)
    inputs = {
        "tax_history": _load_jsonl(args.tax_history),
        "complaints": _load_jsonl(args.complaints),
        "plats": _load_jsonl(args.plats),
        "permits": _load_jsonl(args.permits),
        "addresses": _load_jsonl(args.addresses),
        "catalyst_events": _load_jsonl(args.catalyst_events),
    }
    result = run(_load_jsonl(args.signals), _load_parcels(args.parcels), _load_jsonl(args.comps),
                 today, args.year, cfgs, inputs=inputs, value_threshold=args.value_threshold,
                 market_report=market_report)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "offer_sheet.jsonl"), "w", encoding="utf-8") as f:
        for r in result["offer_sheet"]:
            f.write(json.dumps(r) + "\n")

    if args.dsn:
        from offer_db import write_offer_sheet_db
        print(f"Upserted {write_offer_sheet_db(args.dsn, result['offer_sheet'])} offer-sheet rows into leads.")

    mc = result.get("market_context")
    if mc:
        print(f"MARKET POSTURE: {mc['stance']}  (sourcing {mc['sourcing_tailwinds']}, "
              f"exit-caution {mc['exit_cautions']})")
        for n in mc["notes"]:
            print(f"  • {n}")
    print(f"Augmented signals: {result['augment_counts']}")
    print(f"Signals live/dropped: {result['signals_live']}/{result['signals_dropped']}  "
          f"Leads: {result['leads']}")
    print("\n=== OFFER SHEET (top 12) ===")
    for r in result["offer_sheet"][:12]:
        val = r["valuation"]
        est = f"${val['estimated_value']:,}" if val and val.get("status") == "valued" else "-"
        band = ""
        if val and val.get("status") == "valued":
            ob = val["offer_band"]
            hi = ob.get("market_adjusted_high", ob["as_is_high"])
            band = f"  offer ${ob['as_is_low']:,}-${hi:,}"
            if "market_adjusted_high" in ob:
                band += "*"
        wn = f" why-now {r['why_now']}" if r["why_now"] is not None else ""
        print(f"  {r['situs_address'] or r['apn']:<22} score {r['seller_opportunity_score']:>3} "
              f"[{r['band']}] conf {r['confidence']}{wn}  est {est}{band}")
        print(f"      signals: {', '.join(r['top_signals'])}")
    print(f"\nWrote {args.outdir}/offer_sheet.jsonl")


if __name__ == "__main__":
    main()
