#!/usr/bin/env python3
"""Read the store back out: daily cohorts, the live lead list, transfers, outcomes.

    python3 report.py --db monitorclt.sqlite daily            # new targets per pull date
    python3 report.py --db monitorclt.sqlite daily --by filing_date
    python3 report.py --db monitorclt.sqlite leads            # confirmed, not transferred
    python3 report.py --db monitorclt.sqlite transfers        # everything that left an estate
    python3 report.py --db monitorclt.sqlite outcomes         # how the model's calls held up

`daily --by as_of` counts by the pull that first surfaced each pair -- that is
"what arrived today". `--by filing_date` counts by when the estate was opened,
which is the only way to look back before the store existed: load one
historical pull and the cohorts fall out by filing date. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import store  # noqa: E402  (path must be set first)

COHORT_KEYS = {"as_of": "m.first_seen_as_of", "filing_date": "e.filing_date"}


def daily(conn, by="as_of", county=None):
    """Per date: pairs first seen as confirmed / pending, and pairs that transferred."""
    key = COHORT_KEYS[by]
    where, args = "", []
    if county:
        where, args = " WHERE e.county = ?", [county]
    rows = defaultdict(lambda: {"new_confirmed": 0, "new_pending": 0, "transferred": 0, "estates": set()})
    for r in conn.execute(
        """SELECT {0} AS day, m.left_id, m.status, m.transferred_as_of, o.status AS first_status
           FROM entity_match m
           JOIN estate_case e ON e.left_id = m.left_id
           JOIN match_observation o ON o.run_id = m.first_seen_run AND o.left_id = m.left_id AND o.right_id = m.right_id{1}""".format(key, where),
        args,
    ):
        day = r["day"] or "unknown"
        if r["first_status"] == "confirmed":
            rows[day]["new_confirmed"] += 1
            rows[day]["estates"].add(r["left_id"])
        elif r["first_status"] == "pending":
            rows[day]["new_pending"] += 1
        if r["status"] == "transferred":
            tday = r["transferred_as_of"] if by == "as_of" else day
            rows[tday or "unknown"]["transferred"] += 1
    out, active = [], 0
    for day in sorted(rows):
        row = rows[day]
        active += row["new_confirmed"] - row["transferred"]
        out.append(
            {
                "day": day,
                "new_confirmed": row["new_confirmed"],
                "new_pending": row["new_pending"],
                "transferred": row["transferred"],
                "estates_with_new_confirmed": len(row["estates"]),
                "active_confirmed": active,
            }
        )
    return out


def leads(conn, county=None):
    """The lead list: confirmed and still in the estate."""
    where, args = "WHERE m.status = 'confirmed'", []
    if county:
        where += " AND e.county = ?"
        args.append(county)
    return [
        dict(r)
        for r in conn.execute(
            """SELECT m.left_id, m.right_id, e.decedent_name, e.personal_rep_name, e.filing_date, m.score, m.match_tier,
                      m.first_seen_as_of, p.situs_address, p.owner_name, p.assessed_value
               FROM entity_match m
               JOIN estate_case e ON e.left_id = m.left_id
               LEFT JOIN parcel_snapshot p ON p.right_id = m.right_id AND p.run_id = m.last_seen_run
               {0} ORDER BY m.first_seen_as_of DESC, m.score DESC, m.left_id""".format(where),
            args,
        )
    ]


def transfer_events(conn):
    return [dict(r, detail=json.loads(r["detail"])) for r in conn.execute("SELECT * FROM parcel_transfer ORDER BY as_of, id")]


def outcomes(conn):
    """What later records said about the model's own calls, by tier and by evidence.

    A `deed_from_estate` on a pair the model confirmed is a true positive it
    earned; a `namesake_conveyance` is a false positive it made. Everything else
    is still unknown. The tallies are the live version of evaluate.py's
    precision-by-evidence, built from real conveyances instead of hand labels.
    """
    verdict = {}
    for r in conn.execute("SELECT left_id, right_id, kind FROM parcel_transfer WHERE kind IN ('deed_from_estate', 'namesake_conveyance')"):
        key = (r["left_id"], r["right_id"])
        if r["kind"] == "deed_from_estate":
            verdict[key] = True
        else:
            verdict.setdefault(key, False)
    by_tier = defaultdict(lambda: {"tp": 0, "fp": 0, "unknown": 0})
    by_evidence = defaultdict(lambda: {"tp": 0, "fp": 0, "unknown": 0})
    for m in conn.execute("SELECT left_id, right_id, match_tier, evidence, model_status FROM entity_match WHERE model_status IN ('confirmed', 'pending')"):
        label = verdict.get((m["left_id"], m["right_id"]))
        bucket = "unknown" if label is None else ("tp" if label else "fp")
        by_tier[m["match_tier"]][bucket] += 1
        for e in json.loads(m["evidence"]):
            by_evidence[e][bucket] += 1
    return {"by_tier": _finish(by_tier), "by_evidence": _finish(by_evidence), "labeled": len(verdict)}


def _finish(table):
    out = []
    for name in sorted(table):
        row = dict(table[name], name=name)
        total = row["tp"] + row["fp"]
        row["precision"] = round(row["tp"] / total, 4) if total else None
        out.append(row)
    return out


# ----------------------------------------------------------------- report ---


def format_daily(rows, by):
    lines = ["NEW TARGETS BY {0}".format("PULL DATE" if by == "as_of" else "ESTATE FILING DATE")]
    lines.append("  date        new confirmed  new pending  transferred  estates  active confirmed")
    for r in rows:
        lines.append(
            "  {0:<10}  {1:>13}  {2:>11}  {3:>11}  {4:>7}  {5:>16}".format(
                r["day"], r["new_confirmed"], r["new_pending"], r["transferred"], r["estates_with_new_confirmed"], r["active_confirmed"]
            )
        )
    if not rows:
        lines.append("  (nothing loaded yet)")
    return "\n".join(lines)


def format_leads(rows):
    lines = ["LEADS -- confirmed, not transferred ({0})".format(len(rows))]
    for r in rows:
        lines.append("  {0}  {1}  since {2}  {3:.3f} {4}".format(r["left_id"], r["right_id"], r["first_seen_as_of"], r["score"], r["match_tier"]))
        lines.append("      {0} / rep {1} / filed {2}".format(r["decedent_name"], r["personal_rep_name"] or "-", r["filing_date"] or "-"))
        if r["situs_address"] or r["owner_name"]:
            lines.append("      {0}  owner: {1}".format(r["situs_address"] or "", r["owner_name"] or ""))
    return "\n".join(lines)


def format_transfers(events):
    lines = ["TRANSFERS ({0})".format(len(events))]
    for e in events:
        lines.append("  {0}  [{1}] {2} -> {3}: {4} -> {5}".format(e["as_of"], e["kind"], e["left_id"], e["right_id"], e["status_before"], e["status_after"]))
        d = e["detail"]
        if "grantee_name" in d:
            lines.append(
                "      {0} {1}: {2} -> {3}  ({4}; grantee: {5})".format(
                    d.get("recorded_date"), d.get("instrument_type") or "deed", d.get("grantor_name"), d.get("grantee_name"), d["basis"], d["grantee_relation"]
                )
            )
        elif "owner_after" in d:
            lines.append("      owner: {0} -> {1}".format(d["owner_before"], d["owner_after"]))
        elif "last_sale_after" in d:
            lines.append("      last sale: {0} -> {1}".format(d["last_sale_before"], d["last_sale_after"]))
    return "\n".join(lines)


def format_outcomes(summary):
    lines = ["OUTCOMES from recorded conveyances ({0} pairs settled)".format(summary["labeled"])]
    for title, key in (("BY TIER", "by_tier"), ("BY EVIDENCE", "by_evidence")):
        lines.append(title)
        for row in summary[key]:
            pct = "  n/a " if row["precision"] is None else "{0:5.1f}%".format(row["precision"] * 100)
            lines.append("  {0:<28} {1}  tp {2:<4} fp {3:<4} unknown {4}".format(row["name"], pct, row["tp"], row["fp"], row["unknown"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--county")
    parser.add_argument("--json", dest="json_out")
    sub = parser.add_subparsers(dest="command", required=True)
    p_daily = sub.add_parser("daily")
    p_daily.add_argument("--by", choices=sorted(COHORT_KEYS), default="as_of")
    sub.add_parser("leads")
    sub.add_parser("transfers")
    sub.add_parser("outcomes")
    args = parser.parse_args(argv)

    conn = store.connect(args.db)
    if args.command == "daily":
        data = daily(conn, args.by, args.county)
        text = format_daily(data, args.by)
    elif args.command == "leads":
        data = leads(conn, args.county)
        text = format_leads(data)
    elif args.command == "transfers":
        data = transfer_events(conn)
        text = format_transfers(data)
    else:
        data = outcomes(conn)
        text = format_outcomes(data)
    conn.close()
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
