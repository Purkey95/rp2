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
  capped         the pair was reached through a business entity or a trustee
                 and is pending by construction (crossref.CAP_AT_PENDING) --
                 review reaches it, no threshold does

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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)

TOOL_VERSION = "1.1"
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
            table[key].add("{0}/{1}".format(record.get("county"), record.get(id_field)))

    def resolve(value):
        key = _norm_key(value)
        if "/" in str(value or ""):
            return str(value)
        found = table.get(key)
        if not found:
            return None
        if len(found) > 1:
            raise ValueError(
                "{0} {1!r} exists in {2} counties; label it as COUNTY/{1}".format(
                    label, value, len(found)
                )
            )
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
                        "reason": "no {0} record in the input".format(
                            "estate case" if not left else "parcel"
                        ),
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
    status = crossref.disposition(link["score"], evidence, party, same_county, swept)[0]
    # The cap is keyed on evidence precisely so it survives this reconstruction.
    # If it ever does not, that is a bug in crossref, not a data point.
    if status == "confirmed" and crossref.cap_flags(evidence):
        raise RuntimeError(
            "capped link confirmed at threshold {0}: {1} -> {2}".format(
                threshold, link["left_id"], link["right_id"]
            )
        )
    return status


def confusion(links, labels, threshold, rules):
    """Confusion matrix at one threshold, counting blocked-out positives as misses."""
    candidates = {(link["left_id"], link["right_id"]): link for link in links}
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    reviewable = 0
    blocked_out, scored_low, false_positives, capped = [], [], [], []

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
        if crossref.cap_flags(link["evidence"]):
            # Pending by construction: a true match here is a miss at auto-confirm
            # (it never reaches the lead list) but not a weights problem, and a
            # non-match can never be a false positive.
            counts["fn" if is_match else "tn"] += 1
            capped.append(
                {
                    "left_id": left,
                    "right_id": right,
                    "score": link["score"],
                    "status": status,
                    "is_match": is_match,
                    "flags": link["flags"],
                    "evidence": link["evidence"],
                }
            )
        elif status == "confirmed":
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
        "capped": capped,
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
    rows = defaultdict(lambda: {"tp": 0, "fp": 0, "unlabeled": 0})
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
        row = dict(rows[name])
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
    clearing = [
        p
        for p in points
        if p["precision"] is not None and p["precision"] >= target_precision and p["recall"]
    ]
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
    return "  n/a " if value is None else "{0:5.1f}%".format(value * 100)


def _format_errors(point):
    """The three ways the run was wrong, kept apart because their fixes differ."""
    lines = []
    if point["false_positives"]:
        lines.append("FALSE POSITIVES (auto-confirmed, labeled non-match)")
        for row in point["false_positives"]:
            lines.append(
                "  {0:.3f}  {1} -> {2}".format(row["score"], row["left_id"], row["right_id"])
            )
            lines.append(
                "         tier {0} | {1}".format(row["match_tier"], ", ".join(row["evidence"]))
            )
        lines.append("")

    if point["blocked_out"]:
        lines.append("MISSED -- never became a candidate (blocking/parsing, not weights)")
        for row in point["blocked_out"]:
            lines.append("  {0} -> {1}".format(row["left_id"], row["right_id"]))
        lines.append("")

    if point["scored_low"]:
        lines.append("MISSED -- scored but not auto-confirmed (weights)")
        for row in point["scored_low"]:
            lines.append(
                "  {0:.3f} [{1:<9}] {2} -> {3}".format(
                    row["score"], row["status"], row["left_id"], row["right_id"]
                )
            )
            lines.append("         {0}".format(", ".join(row["evidence"])))
        lines.append("")

    if point["capped"]:
        lines.append(
            "CAPPED -- pending by construction (held_via_entity / held_in_trust); "
            "review reaches these, no threshold does"
        )
        for row in point["capped"]:
            lines.append(
                "  {0:.3f} [{1:<9}] {2} -> {3}  labeled {4}".format(
                    row["score"], row["status"], row["left_id"], row["right_id"],
                    "match" if row["is_match"] else "non-match",
                )
            )
            lines.append("         {0}".format(", ".join(row["evidence"])))
        lines.append("")
    return lines


def format_report(ev):
    run = ev["run"]
    point = ev["at_current_threshold"]
    counts = point["counts"]
    lines = [
        "MonitorCLT probate cross-reference -- calibration report",
        "  tool {0} / rules {1} | auto_confirm {2}, review_floor {3}".format(
            run["tool_version"], run["rules_version"], run["auto_confirm"], run["review_floor"]
        ),
        "  {0} labeled pairs ({1} true matches) | {2} candidates generated, {3} unlabeled".format(
            run["labeled_pairs"],
            run["labeled_positive"],
            run["candidates_generated"],
            run["candidates_unlabeled"],
        ),
        "",
        "AT THE SHIPPED THRESHOLD",
        "  auto-confirm    precision {0}  recall {1}  F1 {2}".format(
            _pct(point["precision"]), _pct(point["recall"]), point["f1"]
        ),
        "  through review  recall    {0}   <- ceiling a reviewer could reach".format(
            _pct(point["review_recall"])
        ),
        "  tp {0}  fp {1}  fn {2}  tn {3}".format(
            counts["tp"], counts["fp"], counts["fn"], counts["tn"]
        ),
        "",
    ]

    lines.extend(_format_errors(point))

    for title, key in (("PRECISION BY TIER", "by_tier"), ("PRECISION BY EVIDENCE", "by_evidence")):
        if ev[key]:
            lines.append(title)
            for row in ev[key]:
                lines.append(
                    "  {0:<26} {1}  tp {2:<4} fp {3:<4} unlabeled {4}".format(
                        row["name"], _pct(row["precision"]), row["tp"], row["fp"], row["unlabeled"]
                    )
                )
            lines.append("")

    lines.append("THRESHOLD SWEEP")
    lines.append("  thresh  precision  recall     F1     tp   fp   fn")
    for p in ev["sweep"]:
        lines.append(
            "  {0:.2f}    {1}     {2}   {3:.4f}   {4:<4} {5:<4} {6}".format(
                p["threshold"],
                _pct(p["precision"]),
                _pct(p["recall"]),
                p["f1"],
                p["counts"]["tp"],
                p["counts"]["fp"],
                p["counts"]["fn"],
            )
        )
    lines.append("")

    rec = ev["recommendation"]
    lines.append("RECOMMENDATION")
    if rec["best_f1"]:
        lines.append(
            "  best F1          {0:.2f}  (F1 {1:.4f}, precision {2}, recall {3})".format(
                rec["best_f1"]["threshold"],
                rec["best_f1"]["f1"],
                _pct(rec["best_f1"]["precision"]),
                _pct(rec["best_f1"]["recall"]),
            )
        )
    cheapest = rec["lowest_threshold_meeting_target"]
    if cheapest:
        lines.append(
            "  precision >= {0:.0%}  {1:.2f}  (precision {2}, recall {3})  <- ship this one".format(
                rec["target_precision"],
                cheapest["threshold"],
                _pct(cheapest["precision"]),
                _pct(cheapest["recall"]),
            )
        )
    else:
        lines.append(
            "  no threshold reaches precision {0:.0%} with any recall -- the weights, "
            "not the cutoff, are the problem".format(rec["target_precision"])
        )

    if ev["unresolved_labels"]:
        lines.append("")
        lines.append("UNUSED LABEL ROWS ({0})".format(len(ev["unresolved_labels"])))
        for row in ev["unresolved_labels"][:20]:
            lines.append("  {0}: {1}".format(row["reason"], json.dumps(row["row"], sort_keys=True)))

    lines.append("")
    lines.append(
        "Precision here is measured against your labels only. Label the hard cases --"
    )
    lines.append(
        "common surnames, remarriages, junior/senior pairs -- or this flatters itself."
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estates", required=True)
    parser.add_argument("--parcels", required=True)
    parser.add_argument("--deeds")
    parser.add_argument("--entities", help="NC SOS business entity records (.jsonl or .json)")
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
    entities = crossref.load_records(args.entities) if args.entities else []

    labels, unresolved = load_labels(args.labels, estates, parcels)
    if not labels:
        parser.error("no usable labels: check the header (file_number, pin, is_match)")

    result = crossref.crossref(estates, parcels, deeds, rules, entities=entities)
    ev = evaluate(result, labels, unresolved, rules, args.target_precision)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=2, sort_keys=True)
    if not args.quiet:
        print(format_report(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
