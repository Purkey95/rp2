#!/usr/bin/env python3
"""Market Timing model — the WHEN/WHERE layer.

Pulls the free FRED series in series.json, computes each indicator's level,
6- and 12-month change, direction, and acceleration (the second derivative),
plus the derived indicators (mortgage spread, jobs-to-permits ratio). Groups by
leading / coincident / lagging and prints a decision-oriented read.

This is deliberately SEPARATE from the Owner Distress Score: market indicators
tell you when/where to lean in; parcel signals tell you what to buy. The output
here is meant to MODULATE distress leads (which submarket, how aggressive), not
to be mixed into a parcel's score.

Pure stdlib. Free data, no API key.

    python3 market.py                 # live from FRED
    python3 market.py --offline DIR   # read cached CSVs DIR/<id>.csv (tests/CI)
"""

import argparse
import json
import os

import fred

HERE = os.path.dirname(os.path.abspath(__file__))


def _analyze(series):
    return {
        "latest": fred.latest(series)[1] if series else None,
        "as_of": fred.latest(series)[0] if series else None,
        "chg_6m_pct": _round(fred.pct_change(series, 6)),
        "chg_12m_pct": _round(fred.pct_change(series, 12)),
        "direction_6m": fred.direction(series, 6),
        "acceleration": fred.acceleration(series, 6),
    }


def _round(v):
    return round(v, 2) if v is not None else None


def build_report(cfg, fetch=None):
    data = {}
    for s in cfg["series"]:
        series = fred.fetch_series(s["id"], fetch=fetch)
        data[s["id"]] = series

    indicators = []
    for s in cfg["series"]:
        a = _analyze(data[s["id"]])
        indicators.append({**s, **a})

    derived = []
    # mortgage spread
    m30, t10 = fred.latest(data.get("MORTGAGE30US", [])), fred.latest(data.get("DGS10", []))
    if m30 and t10:
        spread_now = m30[1] - t10[1]
        # spread ~6 months ago
        m30p, t10p = fred.value_n_months_ago(data["MORTGAGE30US"], 6), fred.value_n_months_ago(data["DGS10"], 6)
        spread_6m = (m30p[1] - t10p[1]) if (m30p and t10p) else None
        derived.append({
            "name": "mortgage_spread", "layer": "leading",
            "latest": round(spread_now, 2),
            "chg_6m_abs": round(spread_now - spread_6m, 2) if spread_6m is not None else None,
            "direction_6m": "widening" if (spread_6m is not None and spread_now > spread_6m + 0.05)
                            else ("narrowing" if (spread_6m is not None and spread_now < spread_6m - 0.05) else "flat"),
            "read": "widening = credit tightening / risk-off (hard-money pricing up)",
        })
    # jobs-to-permits ratio (local)
    emp, permits = data.get("CHAR737NA", []), data.get("CHAR737BPPRIV", [])
    jobs_added = fred.abs_change(emp, 12)
    if jobs_added is not None and len(permits) >= 12:
        units = sum(v for _, v in permits[-12:])
        ratio = (jobs_added * 1000) / units if units else None
        if ratio is not None:
            derived.append({
                "name": "jobs_to_permits", "layer": "coincident",
                "latest": round(ratio, 2),
                "read": ("~1.2-1.5 balanced; <1.0 oversupply; >1.5 undersupply. "
                         f"({round(jobs_added*1000):,} jobs / {round(units):,} units, 12mo)"),
            })

    return {"indicators": indicators, "derived": derived}


def synthesis(report):
    """A short, honest rule-based read — not a fabricated single score."""
    idx = {i["id"]: i for i in report["indicators"]}
    der = {d["name"]: d for d in report["derived"]}
    notes = []
    sp = der.get("mortgage_spread")
    if sp and sp["direction_6m"] == "widening":
        notes.append("credit tightening (spread widening) — fewer competing buyers, stress-test your own debt")
    if idx.get("DRTSCLCC", {}).get("direction_6m") == "rising":
        notes.append("banks tightening CRE standards — local lending window narrowing")
    if idx.get("DRCRELEXFACBS", {}).get("direction_6m") == "rising" or idx.get("DRSFRMACBS", {}).get("direction_6m") == "rising":
        notes.append("delinquency rising — distressed-supply tailwind for sourcing, caution on exit")
    if idx.get("CHAR737BPPRIV", {}).get("direction_6m") == "rising":
        notes.append("local permits rising — future supply, discount appreciation assumptions")
    jp = der.get("jobs_to_permits")
    if jp and jp["latest"] is not None:
        if jp["latest"] < 1.0:
            notes.append(f"jobs-to-permits {jp['latest']} (<1.0) — local oversupply risk")
        elif jp["latest"] > 1.5:
            notes.append(f"jobs-to-permits {jp['latest']} (>1.5) — demand outrunning building")
    if idx.get("CHAR737URN", {}).get("direction_6m") == "rising":
        notes.append("local unemployment rising — demand engine cooling")
    return notes or ["no strong directional signals in the current window"]


def main():
    ap = argparse.ArgumentParser(description="MonitorCLT Market Timing model")
    ap.add_argument("--series", default=os.path.join(HERE, "series.json"))
    ap.add_argument("--offline", help="dir of cached <series_id>.csv for offline/CI runs")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    cfg = json.load(open(args.series, encoding="utf-8"))
    fetch = None
    if args.offline:
        def fetch(url):
            sid = url.split("id=")[1].split("&")[0]
            return open(os.path.join(args.offline, f"{sid}.csv"), encoding="utf-8").read()

    report = build_report(cfg, fetch=fetch)
    report["synthesis"] = synthesis(report)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "market_signals.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    for layer in ("leading", "coincident", "lagging"):
        rows = [i for i in report["indicators"] if i["layer"] == layer]
        if not rows:
            continue
        print(f"\n{layer.upper()}")
        for i in rows:
            latest = "n/a" if i["latest"] is None else f"{i['latest']:g}"
            chg = "n/a" if i["chg_6m_pct"] is None else f"{i['chg_6m_pct']:+.1f}%"
            print(f"  {i['label']:<46} {latest:>10}  6m {chg:>7}  "
                  f"{i['direction_6m']:<7} {i['acceleration']}")
    print("\nDERIVED")
    for d in report["derived"]:
        extra = d.get("direction_6m", "")
        latest = "n/a" if d.get("latest") is None else f"{d['latest']:g}"
        print(f"  {d['name']:<46} {latest:>10}  {extra}")
    print("\nREAD (WHEN/WHERE — modulates distress leads, not mixed into the score):")
    for n in report["synthesis"]:
        print(f"  • {n}")
    print(f"\nWrote {args.outdir}/market_signals.json")


if __name__ == "__main__":
    main()
