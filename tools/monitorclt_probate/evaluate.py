#!/usr/bin/env python3
"""Measure the cross-reference against hand-labeled pairs. Calibration, not vibes.

crossref.py ships weights that are defensible starting points, not measured ones.
This scores them: label a few hundred estate/parcel pairs by hand, run this, and
find out what the thresholds are actually buying you.

It reports two operating points, because the pipeline has two:

  auto-confirm   what lands in v_estate_property without a human. A false
                 positive here reaches outreach, so precision is what matters.
  through review confirmed + pending, i.e. what a reviewer would ever see. This
                 is the recall ceiling -- anything rejected or never blocked in
                 is lost no matter how diligent the reviewer is.

And it separates the two ways recall dies, which have different fixes:

  blocked out    the pair never became a candidate (the decedent's FIRST+LAST
                 never matched an owner party) -- a *parsing/blocking* problem,
                 no threshold will recover it
  scored low     the pair was scored and fell short -- a *weights* problem

Per-tier and per-evidence precision show which corroboration is pulling its
weight. The threshold sweep shows where auto_confirm should actually sit: the
best F1, and the cheapest threshold that still clears a target precision.

Labels are CSV with a header: file_number, pin, is_match (1/0). Counties are
resolved from the estate and parcel records, so you only write what you looked
up. Pure stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)  # pylint: disable=wrong-import-position

TOOL_VERSION = "1.0"
TRUE_VALUES = {"1", "TRUE", "T", "YES", "Y", "MATCH"}
FALSE_VALUES = {"0", "FALSE", "F", "NO", "N", "NONMATCH", "NON-MATCH"}


# ----------------------------------------------------------------- labels ---


def _norm_key(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _build_resolver(records, id_field, label):
    """Map a bare file number / PIN onto the full 'COUNTY/value' id used by matches.

    Ambiguity is an error, not a guess: the same file number in two counties means
    the label file has to say which.
    """
    table = defaultdict(set)
    for record in records:
        key = _norm_key(record.get(id_field))
        if key:
            table[key].add(f"{record.get('county')}/{record.get(id_field)}")

    def resolve(value):
        key = _norm_key(value)
        if "/" in str(value or ""):
            return str(value)
        found = table.get(key)
        if not found:
            return None
        if len(found) > 1:
            raise ValueError(f"{label} {value!r} exists in {len(found)} counties; label it as COUNTY/{value}")
        return sorted(found)[0]

    return resolve


def load_labels(path, estates, parcels):
    """Read the label CSV into {(left_id, right_id): bool}, plus rows we could not place."""
    resolve_estate = _build_resolver(estates, "file_number", "estate file number")
    resolve_parcel = _build_resolver(parcels, "pin", "parcel PIN")

    labels, unresolved = {}, []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            raw_estate = row.get("file_number") or row.get("left_id") or ""
            raw_parcel = row.get("pin") or row.get("right_id") or ""
            raw_label = (row.get("is_match") or row.get("label") or "").upper()

            if raw_label in TRUE_VALUES:
                is_match = True
            elif raw_label in FALSE_VALUES:
                is_match = False
            else:
                unresolved.append({"row": row, "reason": "is_match not 1/0"})
                continue

            left, right = resolve_estate(raw_estate), resolve_parcel(raw_parcel)
            if not left or not right:
                unresolved.append(
                    {
                        "row": row,
                        "reason": f"no {'estate case' if not left else 'parcel'} record in the input",
                    }
                )
                continue
            labels[(left, right)] = is_match
    return labels, unresolved


# ---------------------------------------------------------------- scoring ---


def status_at(link, threshold, rules):
    """Re-derive the disposition at an arbitrary auto_confirm threshold.

    The gates are reconstructed from the link's own evidence, so a sweep needs no
    re-run of the matcher and can never drift from what it decided.
    """
    evidence = link["evidence"]
    party = {"is_organization": "organization_owner" in evidence}
    same_county = "county_mismatch" not in evidence
    swept = dict(rules)
    swept["thresholds"] = dict(rules["thresholds"])
    swept["thresholds"]["auto_confirm"] = threshold
    return crossref.disposition(link["score"], evidence, party, same_county, swept)[0]


def confusion(links, labels, threshold, rules):
    """Confusion matrix at one threshold, counting blocked-out positives as misses."""
    candidates = {(link["left_id"], link["right_id"]): link for link in links}
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    reviewable = 0
    blocked_out, scored_low, false_positives = [], [], []

    for (left, right), is_match in sorted(labels.items()):
        link = candidates.get((left, right))
        if link is None:
            if is_match:
                counts["fn"] += 1
                blocked_out.append({"left_id": left, "right_id": right})
            else:
                counts["tn"] += 1  # correctly never even considered
            continue

        status = status_at(link, threshold, rules)
        if status in ("confirmed", "pending") and is_match:
            reviewable += 1
        if status == "confirmed":
            if is_match:
                counts["tp"] += 1
            else:
                counts["fp"] += 1
                false_positives.append(
                    {
                        "left_id": left,
                        "right_id": right,
                        "score": link["score"],
                        "match_tier": link["match_tier"],
                        "evidence": link["evidence"],
                    }
                )
        elif is_match:
            counts["fn"] += 1
            scored_low.append(
                {
                    "left_id": left,
                    "right_id": right,
                    "score": link["score"],
                    "status": status,
                    "evidence": link["evidence"],
                }
            )
        else:
            counts["tn"] += 1

    positives = counts["tp"] + counts["fn"]
    return {
        "threshold": round(threshold, 3),
        "counts": counts,
        "precision": _ratio(counts["tp"], counts["tp"] + counts["fp"]),
        "recall": _ratio(counts["tp"], positives),
        "f1": _f1(counts),
        "review_recall": _ratio(reviewable, positives),
        "blocked_out": blocked_out,
        "scored_low": scored_low,
        "false_positives": false_positives,
    }


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _f1(counts):
    precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
    recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
    if not precision or not recall:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def breakdown(links, labels, threshold, rules, key):
    """Precision per match tier, or per evidence label, among auto-confirmed pairs.

    Evidence rows overlap by design -- one link contributes to every label it
    carries. That is the point: it shows which corroboration keeps its promises.
    """
    rows: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "unlabeled": 0})
    for link in links:
        if status_at(link, threshold, rules) != "confirmed":
            continue
        label = labels.get((link["left_id"], link["right_id"]))
        values = link["evidence"] if key == "evidence" else [link["match_tier"]]
        for value in values:
            if label is None:
                rows[value]["unlabeled"] += 1
            elif label:
                rows[value]["tp"] += 1
            else:
                rows[value]["fp"] += 1
    out = []
    for name in sorted(rows):
        row: Dict[str, Any] = dict(rows[name])
        row["name"] = name
        row["precision"] = _ratio(row["tp"], row["tp"] + row["fp"])
        out.append(row)
    return out


def sweep(links, labels, rules, start=None, stop=1.0, step=0.05):
    """Sweep auto_confirm from the review floor up.

    Thresholds are stepped as integers and rounded, never accumulated: a float
    drifting to 0.8500000000000001 silently drops every link scoring exactly
    0.850 and puts the sweep at odds with the shipped threshold. Below the review
    floor auto_confirm means nothing -- the floor rejects first -- so the sweep
    starts there rather than recommending a cutoff that cannot be shipped.
    """
    start = rules["thresholds"]["review_floor"] if start is None else start
    points = []
    for index in range(int(round((stop - start) / step)) + 1):
        threshold = round(start + index * step, 4)
        point = confusion(links, labels, threshold, rules)
        points.append(
            {
                "threshold": point["threshold"],
                "precision": point["precision"],
                "recall": point["recall"],
                "f1": point["f1"],
                "counts": point["counts"],
            }
        )
    return points


def recommend(points, target_precision):
    """Best F1, and the cheapest threshold still clearing the precision target.

    A wrong confirmed costs far more than a pending, so the target-precision pick
    is usually the one to ship.
    """
    scored = [p for p in points if p["f1"]]
    best_f1 = max(scored, key=lambda p: (p["f1"], p["threshold"])) if scored else None
    clearing = [p for p in points if p["precision"] is not None and p["precision"] >= target_precision and p["recall"]]
    cheapest = min(clearing, key=lambda p: p["threshold"]) if clearing else None
    return {
        "best_f1": best_f1,
        "target_precision": target_precision,
        "lowest_threshold_meeting_target": cheapest,
    }


def evaluate(result, labels, unresolved, rules, target_precision):
    links = result["matches"]
    threshold = rules["thresholds"]["auto_confirm"]
    labeled_pairs = set(labels)
    candidate_pairs = {(link["left_id"], link["right_id"]) for link in links}
    points = sweep(links, labels, rules)
    return {
        "run": {
            "tool_version": TOOL_VERSION,
            "rules_version": rules.get("version"),
            "auto_confirm": threshold,
            "review_floor": rules["thresholds"]["review_floor"],
            "labeled_pairs": len(labels),
            "labeled_positive": sum(1 for v in labels.values() if v),
            "candidates_generated": len(links),
            "candidates_unlabeled": len(candidate_pairs - labeled_pairs),
            "unresolved_label_rows": len(unresolved),
        },
        "at_current_threshold": confusion(links, labels, threshold, rules),
        "by_tier": breakdown(links, labels, threshold, rules, "tier"),
        "by_evidence": breakdown(links, labels, threshold, rules, "evidence"),
        "sweep": points,
        "recommendation": recommend(points, target_precision),
        "unresolved_labels": unresolved,
    }


# ----------------------------------------------------------------- report ---


def _pct(value):
    return "  n/a " if value is None else f"{value * 100:5.1f}%"


def _format_errors(point):
    """The three ways the run was wrong, kept apart because their fixes differ."""
    lines = []
    if point["false_positives"]:
        lines.append("FALSE POSITIVES (auto-confirmed, labeled non-match)")
        for row in point["false_positives"]:
            lines.append(f"  {row['score']:.3f}  {row['left_id']} -> {row['right_id']}")
            lines.append(f"         tier {row['match_tier']} | {', '.join(row['evidence'])}")
        lines.append("")

    if point["blocked_out"]:
        lines.append("MISSED -- never became a candidate (blocking/parsing, not weights)")
        for row in point["blocked_out"]:
            lines.append(f"  {row['left_id']} -> {row['right_id']}")
        lines.append("")

    if point["scored_low"]:
        lines.append("MISSED -- scored but not auto-confirmed (weights)")
        for row in point["scored_low"]:
            lines.append(f"  {row['score']:.3f} [{row['status']:<9}] {row['left_id']} -> {row['right_id']}")
            lines.append(f"         {', '.join(row['evidence'])}")
        lines.append("")
    return lines


def format_report(ev):
    run = ev["run"]
    point = ev["at_current_threshold"]
    counts = point["counts"]
    lines = [
        "MonitorCLT probate cross-reference -- calibration report",
        f"  tool {run['tool_version']} / rules {run['rules_version']} | auto_confirm {run['auto_confirm']}, review_floor {run['review_floor']}",
        f"  {run['labeled_pairs']} labeled pairs ({run['labeled_positive']} true matches)"
        f" | {run['candidates_generated']} candidates generated, {run['candidates_unlabeled']} unlabeled",
        "",
        "AT THE SHIPPED THRESHOLD",
        f"  auto-confirm    precision {_pct(point['precision'])}  recall {_pct(point['recall'])}  F1 {point['f1']}",
        f"  through review  recall    {_pct(point['review_recall'])}   <- ceiling a reviewer could reach",
        f"  tp {counts['tp']}  fp {counts['fp']}  fn {counts['fn']}  tn {counts['tn']}",
        "",
    ]

    lines.extend(_format_errors(point))

    for title, key in (("PRECISION BY TIER", "by_tier"), ("PRECISION BY EVIDENCE", "by_evidence")):
        if ev[key]:
            lines.append(title)
            for row in ev[key]:
                lines.append(f"  {row['name']:<26} {_pct(row['precision'])}  tp {row['tp']:<4} fp {row['fp']:<4} unlabeled {row['unlabeled']}")
            lines.append("")

    lines.append("THRESHOLD SWEEP")
    lines.append("  thresh  precision  recall     F1     tp   fp   fn")
    for p in ev["sweep"]:
        lines.append(
            f"  {p['threshold']:.2f}    {_pct(p['precision'])}     {_pct(p['recall'])}   {p['f1']:.4f}"
            f"   {p['counts']['tp']:<4} {p['counts']['fp']:<4} {p['counts']['fn']}"
        )
    lines.append("")

    rec = ev["recommendation"]
    lines.append("RECOMMENDATION")
    if rec["best_f1"]:
        lines.append(
            f"  best F1          {rec['best_f1']['threshold']:.2f}  (F1 {rec['best_f1']['f1']:.4f},"
            f" precision {_pct(rec['best_f1']['precision'])}, recall {_pct(rec['best_f1']['recall'])})"
        )
    cheapest = rec["lowest_threshold_meeting_target"]
    if cheapest:
        lines.append(
            f"  precision >= {rec['target_precision']:.0%}  {cheapest['threshold']:.2f}"
            f"  (precision {_pct(cheapest['precision'])}, recall {_pct(cheapest['recall'])})  <- ship this one"
        )
    else:
        lines.append(f"  no threshold reaches precision {rec['target_precision']:.0%} with any recall -- the weights, not the cutoff, are the problem")

    if ev["unresolved_labels"]:
        lines.append("")
        lines.append(f"UNUSED LABEL ROWS ({len(ev['unresolved_labels'])})")
        for row in ev["unresolved_labels"][:20]:
            lines.append(f"  {row['reason']}: {json.dumps(row['row'], sort_keys=True)}")

    lines.append("")
    lines.append("Precision here is measured against your labels only. Label the hard cases --")
    lines.append("common surnames, remarriages, junior/senior pairs -- or this flatters itself.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estates", required=True)
    parser.add_argument("--parcels", required=True)
    parser.add_argument("--deeds")
    parser.add_argument("--labels", required=True, help="CSV: file_number, pin, is_match")
    parser.add_argument("--rules")
    parser.add_argument(
        "--target-precision",
        type=float,
        default=0.95,
        help="precision the shipped auto_confirm threshold must clear (default 0.95)",
    )
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    rules = crossref.load_rules(args.rules)
    estates = crossref.load_records(args.estates)
    parcels = crossref.load_records(args.parcels)
    deeds = crossref.load_records(args.deeds) if args.deeds else []

    labels, unresolved = load_labels(args.labels, estates, parcels)
    if not labels:
        parser.error("no usable labels: check the header (file_number, pin, is_match)")

    result = crossref.crossref(estates, parcels, deeds, rules)
    ev = evaluate(result, labels, unresolved, rules, args.target_precision)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=2, sort_keys=True)
    if not args.quiet:
        print(format_report(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
