"""Observe the data, not just the code.

A monitoring product that quietly stops monitoring is worse than none. Every
(connector, county) gets: freshness, current row count, delta vs. its own trailing
history with an anomaly flag, parse-failure rate, schema drift (the field set the
parser saw vs. last time), and the resolver's blocked-out rate; plus rolling
precision from labels. Snapshots are stored so the trend is queryable.
"""

from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional

from .clock import parse_ts
from .store import Store, dumps, loads


def snapshot(store: Store, stale_after_hours: float = 36.0) -> List[Dict[str, Any]]:
    out = []
    pairs = store.query("SELECT DISTINCT connector, county FROM ingest_run ORDER BY connector, county")
    now = parse_ts(store.now())
    blocked_rate = _blocked_out_rate(store)
    precision = _rolling_precision(store)
    for p in pairs:
        runs = store.query("SELECT * FROM ingest_run WHERE connector = ? AND county = ? ORDER BY id DESC LIMIT 20", (p["connector"], p["county"]))
        last = runs[0]
        last_ok = next((r for r in runs if r["status"] == "ok"), None)
        anomalies: List[str] = []
        freshness = None
        if last_ok and last_ok["finished_at"]:
            freshness = round((now - parse_ts(last_ok["finished_at"])).total_seconds() / 3600, 2)
            if freshness > stale_after_hours:
                anomalies.append("stale:{0}h".format(freshness))
        else:
            anomalies.append("never_succeeded")
        if last["status"] == "failed":
            anomalies.append("last_run_failed")
        rows_current = int(
            store.scalar(
                "SELECT COUNT(*) FROM record_version WHERE source = ? AND county = ? AND superseded_at IS NULL AND retired = 0", (p["connector"], p["county"])
            )
            or 0
        )
        delta = int(last["rows_new"]) + int(last["rows_changed"]) + int(last["rows_retired"])
        history_deltas = [int(r["rows_new"]) + int(r["rows_changed"]) + int(r["rows_retired"]) for r in runs[1:] if r["status"] == "ok"]
        if len(history_deltas) >= 3:
            mean = statistics.mean(history_deltas)
            sd = statistics.pstdev(history_deltas)
            if sd and abs(delta - mean) > 3 * sd:
                anomalies.append("delta_anomaly:{0}_vs_mean_{1:.1f}".format(delta, mean))
        seen = int(last["rows_seen"])
        if seen == 0 and any(int(r["rows_seen"]) > 0 for r in runs[1:]):
            anomalies.append("zero_rows")
        fail_rate = round(int(last["rows_failed"]) / seen, 4) if seen else (1.0 if int(last["rows_failed"]) else 0.0)
        if fail_rate > 0.05:
            anomalies.append("parse_failures:{0:.1%}".format(fail_rate))
        prev_fields = next((json.loads(r["field_set"]) for r in runs[1:] if r["status"] == "ok" and r["field_set"] not in ("[]", None)), None)
        cur_fields = json.loads(last["field_set"] or "[]")
        if prev_fields and cur_fields and set(prev_fields) != set(cur_fields):
            gone = sorted(set(prev_fields) - set(cur_fields))
            new = sorted(set(cur_fields) - set(prev_fields))
            anomalies.append("schema_drift:-{0}+{1}".format(",".join(gone) or "-", ",".join(new) or "-"))
        row = {
            "at": store.now(),
            "connector": p["connector"],
            "county": p["county"],
            "freshness_hours": freshness,
            "rows_current": rows_current,
            "rows_delta": delta,
            "parse_fail_rate": fail_rate,
            "blocked_out_rate": blocked_rate if p["connector"] == "estate_case" else None,
            "rolling_precision": precision if p["connector"] == "estate_case" else None,
            "anomalies": dumps(anomalies),
        }
        store.insert("quality_snapshot", row)
        row["anomalies"] = anomalies
        out.append(row)
    store.commit()
    return out


def _blocked_out_rate(store: Store) -> Optional[float]:
    run = store.one("SELECT subjects, blocked_out FROM match_run ORDER BY id DESC LIMIT 1")
    if not run or not run["subjects"]:
        return None
    return round(int(run["blocked_out"]) / int(run["subjects"]), 4)


def _rolling_precision(store: Store, window: int = 200) -> Optional[float]:
    rows = store.query(
        "SELECT l.is_match FROM label l JOIN entity_match m ON m.kind = l.kind AND m.left_id = l.left_id AND m.right_id = l.right_id "
        "WHERE m.status = 'confirmed' AND m.decided_by = 'rules' ORDER BY l.id DESC LIMIT ?",
        (window,),
    )
    if not rows:
        return None
    return round(sum(int(r["is_match"]) for r in rows) / len(rows), 4)


def latest(store: Store) -> List[Dict[str, Any]]:
    rows = store.query(
        "SELECT * FROM quality_snapshot WHERE id IN (SELECT MAX(id) FROM quality_snapshot GROUP BY connector, county) ORDER BY connector, county"
    )
    for r in rows:
        r["anomalies"] = loads(r["anomalies"], [])
    return rows


def format_status(rows: List[Dict[str, Any]]) -> str:
    lines = ["MonitorCLT data status", "", "  connector          county        fresh(h)  rows   delta  fail%   anomalies"]
    for r in rows:
        lines.append(
            "  {0:<18} {1:<12} {2:>8} {3:>6} {4:>6}  {5:>5}   {6}".format(
                r["connector"],
                r["county"],
                "-" if r["freshness_hours"] is None else "{0:.1f}".format(r["freshness_hours"]),
                r["rows_current"],
                r["rows_delta"],
                "{0:.1f}".format(100 * (r["parse_fail_rate"] or 0)),
                ", ".join(r["anomalies"]) or "ok",
            )
        )
    est = next((r for r in rows if r["connector"] == "estate_case"), None)
    if est:
        lines.append("")
        lines.append(
            "  resolver: blocked-out rate {0}, rolling auto-confirm precision {1}".format(
                "n/a" if est["blocked_out_rate"] is None else "{0:.1%}".format(est["blocked_out_rate"]),
                "n/a (no labels)" if est["rolling_precision"] is None else "{0:.1%}".format(est["rolling_precision"]),
            )
        )
    return "\n".join(lines)
