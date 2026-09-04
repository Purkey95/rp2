#!/usr/bin/env python3
"""Point-in-time backtest: what would the matcher have said on a past date, and
what did the deeds recorded since then prove?

Pick a cut-off. Estates filed by then are the cases; deeds recorded by then are
the evidence the matcher is allowed to see; deeds recorded *after* it are the
answer key it never saw. An executor's or administrator's deed, or a deed from
"ESTATE OF <decedent>", conveying a parcel proves that estate held it (a
positive label). A plain warranty deed signed in the decedent's bare name after
the date of death proves the opposite: a living namesake owned that parcel
(a negative). transfers.py holds that reading; evaluate.py does the scoring.

The catch, and why this needs care: the parcel index you can pull today shows
the *buyer*, not the owner the matcher would have seen at the cut-off. A parcel
that sold out of an estate no longer carries the decedent's name, so blocking
fails and the very cases you want to learn from vanish. For every parcel with
a later deed this reconstructs the cut-off owner from the deed chain -- the
grantee of the last deed before the cut-off; failing that, the grantor of the
first deed after it -- and reports how many it had to reconstruct, and how. A
snapshot you actually stored at the time (store.py) is always better than a
reconstruction; this is for the months before there was a store.

    python3 backtest.py --estates e.jsonl --parcels p.jsonl --deeds d.jsonl \
        --start 2026-01-01 --as-of 2026-03-31 --as-of 2026-06-30 --labels-out labels.csv

Pure stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import evaluate  # noqa: E402
import transfers  # noqa: E402

TOOL_VERSION = "1.0"


# ----------------------------------------------------------------- inputs ---


def split_deeds(deeds, as_of):
    """Deeds the matcher may see (recorded by the cut-off, or undated) vs the answer key."""
    known, later, undated = [], [], 0
    for deed in deeds or []:
        recorded = (deed.get("recorded_date") or "")[:10]
        if not recorded:
            undated += 1
            known.append(deed)
        elif recorded <= as_of:
            known.append(deed)
        else:
            later.append(deed)
    return known, later, undated


def estates_in_window(estates, start, as_of):
    out = []
    for estate in estates:
        filed = (estate.get("filing_date") or "")[:10]
        if filed and filed <= as_of and (not start or filed >= start):
            out.append(estate)
    return out


def roll_back_parcels(parcels, known, later, rules, mode="chain"):
    """Reconstruct the owner string each later-conveyed parcel carried at the cut-off.

    mode: 'chain' (deed chain first, later grantor as fallback), 'grantor' (later
    grantor only), 'none' (use the index as pulled -- shows how badly today's
    owner strings hide the past). Fields the cut-off assessor row could not have
    had (the buyer's mailing address, the later sale date) are blanked.
    """
    first_later = {}
    for deed in sorted(later, key=lambda d: (d.get("recorded_date") or "", transfers.deed_key(d))):
        key = transfers.deed_parcel_key(deed)
        if key and key not in first_later:
            first_later[key] = deed
    last_known = {}
    for deed in sorted(known, key=lambda d: (d.get("recorded_date") or "", transfers.deed_key(d))):
        key = transfers.deed_parcel_key(deed)
        if key and deed.get("grantee_name"):
            last_known[key] = deed

    out, how = [], defaultdict(int)
    for parcel in parcels:
        key = crossref.pin_key(parcel.get("county"), parcel.get("pin"))
        if mode == "none" or key not in first_later:
            out.append(parcel)
            continue
        rolled = dict(parcel)
        if mode == "chain" and key in last_known:
            rolled["owner_name"] = last_known[key]["grantee_name"]
            source = "chain_of_title"
        else:
            grantor = first_later[key]
            rolled["owner_name"] = grantor.get("grantor_name")
            fiduciary = transfers.FIDUCIARY_INSTRUMENT.search(crossref.clean_text(grantor.get("instrument_type")))
            marked = any(
                p["markers"] for p in crossref.split_parties(grantor.get("grantor_name", ""), rules.get("name_formats", {}).get("deed", "last_first"), rules)
            )
            source = "later_grantor_fiduciary" if (fiduciary or marked) else "later_grantor"
        for field in ("owner_mailing_address", "last_sale_date", "deed_book", "deed_page"):
            rolled[field] = None
        rolled["_reconstructed_from"] = source
        how[source] += 1
        out.append(rolled)
    return out, dict(how)


# ----------------------------------------------------------------- labels ---


def derive_labels(estates, parcels, later, rules):
    """Labels from the answer key: {(left_id, right_id): bool}, plus where each came from."""
    right_ids = {crossref.pin_key(p.get("county"), p.get("pin")): "{0}/{1}".format(p.get("county"), p.get("pin")) for p in parcels}
    by_key = defaultdict(list)
    for deed in later:
        key = transfers.deed_parcel_key(deed)
        if key:
            by_key[key].append(deed)

    labels, provenance = {}, []
    for estate in estates:
        left = "{0}/{1}".format(estate.get("county"), estate.get("file_number"))
        for key in sorted(by_key):
            readings = [r for r in (transfers.classify_conveyance(d, estate, rules) for d in by_key[key]) if r]
            estate_readings = [r for r in readings if r["relation"] == "estate"]
            namesake_readings = [r for r in readings if r["relation"] == "namesake"]
            if not estate_readings and not namesake_readings:
                continue
            right = right_ids.get(key)
            in_index = right is not None
            if not in_index:
                county, _, pin = key.partition("/")
                right = "{0}/{1}".format(county, by_key[key][0].get("parcel_pin") or pin)
            reading = (estate_readings or namesake_readings)[0]
            labels[(left, right)] = bool(estate_readings)
            provenance.append(
                {
                    "left_id": left,
                    "right_id": right,
                    "is_match": bool(estate_readings),
                    "basis": reading["basis"],
                    "instrument": reading["instrument"],
                    "recorded_date": reading["recorded_date"],
                    "grantee_relation": reading["grantee_relation"],
                    "parcel_in_index": in_index,
                }
            )
    return labels, provenance


# ---------------------------------------------------------------- backtest --


def backtest(estates, parcels, deeds, rules, as_of, start=None, target_precision=0.95, rollback="chain"):
    cases = estates_in_window(estates, start, as_of)
    known, later, undated = split_deeds(deeds, as_of)
    rolled, how = roll_back_parcels(parcels, known, later, rules, rollback)
    result = crossref.crossref(cases, rolled, known, rules)
    labels, provenance = derive_labels(cases, rolled, later, rules)
    ev = evaluate.evaluate(result, labels, [], rules, target_precision) if labels else None

    reconstructed = {p["_reconstructed_from"] for p in rolled if p.get("_reconstructed_from")}
    fiduciary_keys = {crossref.pin_key(p.get("county"), p.get("pin")) for p in rolled if p.get("_reconstructed_from") == "later_grantor_fiduciary"}
    blocked = ev["at_current_threshold"]["blocked_out"] if ev else []
    unrecoverable = [b for b in blocked if crossref.pin_key(*b["right_id"].partition("/")[::2]) in fiduciary_keys]
    not_in_index = [p for p in provenance if not p["parcel_in_index"]]

    return {
        "backtest": {
            "tool_version": TOOL_VERSION,
            "rules_version": rules.get("version"),
            "as_of": as_of,
            "start": start,
            "estates_in_window": len(cases),
            "estates_in_file": len(estates),
            "deeds_known": len(known),
            "deeds_later": len(later),
            "deeds_undated": undated,
            "rollback": rollback,
            "parcels_reconstructed": how,
            "labels_positive": sum(1 for v in labels.values() if v),
            "labels_negative": sum(1 for v in labels.values() if not v),
            "positives_not_in_parcel_index": len(not_in_index),
            "blocked_out_unrecoverable": len(unrecoverable),
            "_sources": sorted(reconstructed),
        },
        "labels": provenance,
        "result": result,
        "evaluation": ev,
    }


# ----------------------------------------------------------------- report ---


def format_report(bt):
    meta = bt["backtest"]
    lines = [
        "MonitorCLT probate cross-reference -- backtest as of {0}".format(meta["as_of"]),
        "  estates filed {0}..{1}: {2} of {3} in file".format(meta["start"] or "(any)", meta["as_of"], meta["estates_in_window"], meta["estates_in_file"]),
        "  deeds: {0} known at cut-off ({1} undated, treated as known), {2} recorded after = answer key".format(
            meta["deeds_known"], meta["deeds_undated"], meta["deeds_later"]
        ),
    ]
    how = meta["parcels_reconstructed"]
    if how:
        lines.append(
            "  cut-off owner reconstructed for {0} parcels: {1}".format(sum(how.values()), ", ".join("{0} {1}".format(v, k) for k, v in sorted(how.items())))
        )
    lines.append("  labels from later conveyances: {0} positive, {1} negative".format(meta["labels_positive"], meta["labels_negative"]))
    if meta["positives_not_in_parcel_index"]:
        lines.append("  {0} labeled parcel(s) absent from the parcel file -- an input gap, not a model miss".format(meta["positives_not_in_parcel_index"]))
    lines.append("")
    if bt["evaluation"] is None:
        lines.append("No later deed involves any estate in the window: nothing to score yet.")
        return "\n".join(lines)

    lines.append(evaluate.format_report(bt["evaluation"]))
    lines.append("")
    lines.append("BACKTEST CAVEATS")
    lines.append("  Positives exist only for estates that conveyed a parcel after the cut-off; an")
    lines.append("  estate still holding its property produces no label, so recall here is recall")
    lines.append("  on the subset that later sold. Negatives come only from a post-death deed in")
    lines.append("  the decedent's bare name; a pair nothing ever contradicts stays unlabeled.")
    if meta["blocked_out_unrecoverable"]:
        lines.append("  {0} missed positive(s) sit on parcels whose cut-off owner could only be".format(meta["blocked_out_unrecoverable"]))
        lines.append("  reconstructed from a fiduciary grantor (no earlier deed in the pull): the")
        lines.append("  deed chain, not the matcher, is what fell short there. Pull deeds further back.")
    return "\n".join(lines)


def format_summary(runs):
    lines = ["BACKTEST SUMMARY", "  as of       estates  labels(+/-)  precision  recall   via review  blocked  fp"]
    for bt in runs:
        meta, ev = bt["backtest"], bt["evaluation"]
        if ev is None:
            lines.append(
                "  {0:<10}  {1:>7}  {2:>4}/{3:<6}  (no labels)".format(
                    meta["as_of"], meta["estates_in_window"], meta["labels_positive"], meta["labels_negative"]
                )
            )
            continue
        point = ev["at_current_threshold"]
        lines.append(
            "  {0:<10}  {1:>7}  {2:>4}/{3:<6}  {4}    {5}   {6}   {7:>7}  {8}".format(
                meta["as_of"],
                meta["estates_in_window"],
                meta["labels_positive"],
                meta["labels_negative"],
                evaluate._pct(point["precision"]),  # pylint: disable=protected-access
                evaluate._pct(point["recall"]),  # pylint: disable=protected-access
                evaluate._pct(point["review_recall"]),  # pylint: disable=protected-access
                len(point["blocked_out"]),
                point["counts"]["fp"],
            )
        )
    return "\n".join(lines)


def write_labels(path, runs):
    """Labels in evaluate.py's CSV format, so they merge with hand labels."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["left_id", "right_id", "is_match", "basis", "instrument", "recorded_date", "grantee_relation", "as_of"])
        seen = set()
        for bt in runs:
            for row in bt["labels"]:
                key = (row["left_id"], row["right_id"])
                if key in seen:
                    continue
                seen.add(key)
                writer.writerow(
                    [
                        row["left_id"],
                        row["right_id"],
                        int(row["is_match"]),
                        row["basis"],
                        row["instrument"],
                        row["recorded_date"],
                        row["grantee_relation"],
                        bt["backtest"]["as_of"],
                    ]
                )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estates", required=True)
    parser.add_argument("--parcels", required=True, help="the parcel index as pulled (today's owners are fine: see rollback)")
    parser.add_argument("--deeds", required=True, help="deeds spanning both sides of the cut-off")
    parser.add_argument("--as-of", dest="as_of", action="append", required=True, help="cut-off date YYYY-MM-DD; repeat for several points")
    parser.add_argument("--start", help="ignore estates filed before this date")
    parser.add_argument("--rollback", choices=("chain", "grantor", "none"), default="chain")
    parser.add_argument("--rules")
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--labels-out", help="write the derived labels as CSV for evaluate.py")
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--summary-only", action="store_true", help="print only the per-date summary table")
    args = parser.parse_args(argv)

    rules = crossref.load_rules(args.rules)
    estates = crossref.load_records(args.estates)
    parcels = crossref.load_records(args.parcels)
    deeds = crossref.load_records(args.deeds)

    runs = [backtest(estates, parcels, deeds, rules, as_of, args.start, args.target_precision, args.rollback) for as_of in sorted(set(args.as_of))]
    if not args.summary_only:
        for bt in runs:
            print(format_report(bt))
            print()
    if len(runs) > 1 or args.summary_only:
        print(format_summary(runs))
    if args.labels_out:
        write_labels(args.labels_out, runs)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump([{k: v for k, v in bt.items() if k != "result"} for bt in runs], f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
