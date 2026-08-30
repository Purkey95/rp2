#!/usr/bin/env python3
"""Temporal Signal Engine — "why this property, why now?"

The five-score model scores a parcel from its CURRENT active signals. This layer
adds the dimension that doc after doc keeps pointing at: TIME. The same four
events mean very different things spread over eight years vs. clustered in three
months. So this reads a per-parcel event TIMELINE and computes a "why now" score
from:

  - recency   — recent events weigh more (decay by age)
  - velocity  — a cluster of events in a short window = escalation
  - sequence  — known escalation chains (code->inspection->demolition; reno->lien)
  - negative space — an expected follow-up that never happened (fire, no repair
                     permit; investor bought, no renovation; eviction, no re-list)

Output per parcel: a why_now score, the matched escalation/negative-space flags,
and the dated event narrative that answers "why this property now."

Events in: JSONL with apn, signal_type, date (YYYY-MM-DD), source_url. Severity
comes from the five-score weights (a foreclosure event weighs more than a permit).
Pass --today for determinism. Pure stdlib.
"""

import argparse
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCORE_WEIGHTS = os.path.join(HERE, "..", "monitorclt_score", "weights.json")


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def load_severity():
    """signal_type -> severity (0-20), from the five-score weights; fallback 5."""
    try:
        cfg = json.load(open(SCORE_WEIGHTS, encoding="utf-8"))
        sev = dict(cfg.get("signal_weights", {}))
        sev.update(cfg.get("derived_weights", {}))
        return sev
    except Exception:
        return {}


def recency_weight(age_days, rules):
    for cutoff, w in rules["recency_tiers_days"]:
        if age_days <= cutoff:
            return w
    return rules["recency_floor"]


def _first_date(events, signal_type):
    ds = [_date(e["date"]) for e in events if e["signal_type"] == signal_type and e.get("date")]
    return min(ds) if ds else None


def detect_escalations(events, rules):
    """A chain fires if its signal_types occur IN ORDER within window_days."""
    hits = []
    for chain in rules["escalation_chains"]:
        seq, last_dt, ok = chain["sequence"], None, True
        first_dt = None
        for st in seq:
            dt = _first_date([e for e in events if (last_dt is None or _date(e["date"]) >= last_dt)], st)
            if dt is None:
                ok = False
                break
            first_dt = first_dt or dt
            last_dt = dt
        if ok and (last_dt - first_dt).days <= chain["window_days"]:
            hits.append({"name": chain["name"], "bonus": chain["bonus"], "why": chain["why"]})
    return hits


def detect_negative_space(events, rules, today):
    """A rule fires if the trigger happened, enough time has passed, and the
    expected follow-up never appeared within the window."""
    hits = []
    by_type = {}
    for e in events:
        by_type.setdefault(e["signal_type"], []).append(_date(e["date"]))
    for rule in rules["negative_space_rules"]:
        trig_dates = by_type.get(rule["trigger"], [])
        if not trig_dates:
            continue
        trig = min(trig_dates)
        # window must have fully elapsed for "it didn't happen" to mean something
        if (today - trig).days < rule["within_days"]:
            continue
        expected = by_type.get(rule["expected"], [])
        followed = any(0 <= (d - trig).days <= rule["within_days"] for d in expected)
        if not followed:
            hits.append({"name": rule["name"], "bonus": rule["bonus"], "flag": rule["flag"]})
    return hits


def score_timeline(events, rules, severity, today):
    events = sorted([e for e in events if e.get("date")], key=lambda e: e["date"])
    if not events:
        return None

    # recency-weighted activity
    activity = 0.0
    for e in events:
        age = (today - _date(e["date"])).days
        sev = severity.get(e["signal_type"], 5)
        activity += recency_weight(age, rules) * sev

    # velocity: events in the trailing window
    window = rules["velocity_window_days"]
    recent = [e for e in events if (today - _date(e["date"])).days <= window]
    cluster = len(recent) >= rules["cluster_threshold"]

    escalations = detect_escalations(events, rules)
    negatives = detect_negative_space(events, rules, today)

    why_now = activity
    if cluster:
        why_now += rules["cluster_bonus"]
    why_now += sum(h["bonus"] for h in escalations)
    why_now += sum(h["bonus"] for h in negatives)
    why_now = round(min(why_now, 100), 1)

    return {
        "why_now": why_now,
        "events_total": len(events),
        "events_recent": len(recent),
        "velocity_cluster": cluster,
        "escalations": escalations,
        "negative_space": negatives,
        "narrative": [f"{e['date']}  {e['signal_type']}" for e in events[-12:]],
    }


def load_events(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def by_parcel(events):
    groups = {}
    for e in events:
        apn = (e.get("apn") or "").strip()
        if apn:
            groups.setdefault(apn, []).append(e)
    return groups


def main():
    ap = argparse.ArgumentParser(description="Temporal Signal Engine (why-now scoring)")
    ap.add_argument("--events", required=True, help="dated events JSONL")
    ap.add_argument("--rules", default=os.path.join(HERE, "rules.json"))
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (determinism)")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    severity = load_severity()
    today = _date(args.today)

    results = []
    for apn, evs in by_parcel(load_events(args.events)).items():
        r = score_timeline(evs, rules, severity, today)
        if r:
            results.append({"apn": apn, **r})
    results.sort(key=lambda r: -r["why_now"])

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "why_now.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    for r in results[:5]:
        tags = [h["name"] for h in r["escalations"]] + [h["flag"] for h in r["negative_space"]]
        print(f"\nAPN {r['apn']}   why_now: {r['why_now']}   "
              f"({r['events_recent']}/{r['events_total']} recent"
              f"{', CLUSTER' if r['velocity_cluster'] else ''})")
        if tags:
            print(f"  flags: {'; '.join(tags)}")
        print("  timeline: " + "  ".join(r["narrative"][-6:]))
    print(f"\nWrote {args.outdir}/why_now.jsonl")


if __name__ == "__main__":
    main()
