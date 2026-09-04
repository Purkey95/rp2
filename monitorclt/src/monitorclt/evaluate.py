"""Measure the resolver against labels. Calibration, not vibes.

Two operating points, because the pipeline has two: auto-confirm (what would reach
export with no human -- precision is what matters) and through-review (confirmed +
pending -- the recall ceiling a reviewer could ever reach). Recall losses are split
into *blocked out* (never a candidate: a parsing/blocking problem no threshold
fixes) and *scored low* (a weights problem). Plus precision per tier and per
evidence label, a threshold sweep, and a reliability table for the probabilities.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from .resolve import gates as G
from .resolve.model import load_rules, reliability
from .resolve.resolver import hydrate
from .review import labels as _labels
from .store import Store


def status_at(match: Dict[str, Any], threshold: float, rules: Dict[str, Any]) -> str:
    """Disposition the rules would give at a different auto_confirm threshold."""
    local = dict(rules, thresholds=dict(rules["thresholds"], auto_confirm=threshold))
    status, _ = G.disposition(match["probability"], match["gates"], match["features"], local)
    return status


def _ratio(n: float, d: float) -> Optional[float]:
    return round(n / d, 4) if d else None


def _f1(c: Dict[str, int]) -> Optional[float]:
    p = _ratio(c["tp"], c["tp"] + c["fp"])
    r = _ratio(c["tp"], c["tp"] + c["fn"])
    if p is None or r is None or (p + r) == 0:
        return None
    return round(2 * p * r / (p + r), 4)


def confusion(matches: Dict[tuple, Dict[str, Any]], labels: List[Dict[str, Any]], threshold: float, rules: Dict[str, Any]) -> Dict[str, Any]:
    auto = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    review = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    misses: List[Dict[str, Any]] = []
    false_confirms: List[Dict[str, Any]] = []
    for lab in labels:
        key = (lab["left_id"], lab["right_id"])
        y = int(lab["is_match"])
        m = matches.get(key)
        status = status_at(m, threshold, rules) if m else "blocked_out"
        auto_pos = status == "confirmed"
        review_pos = status in ("confirmed", "pending")
        for counts, pos in ((auto, auto_pos), (review, review_pos)):
            if y and pos:
                counts["tp"] += 1
            elif y and not pos:
                counts["fn"] += 1
            elif not y and pos:
                counts["fp"] += 1
            else:
                counts["tn"] += 1
        if y and not review_pos:
            misses.append(
                {
                    "left_id": lab["left_id"],
                    "right_id": lab["right_id"],
                    "reason": "blocked_out" if m is None else "scored_low",
                    "probability": m["probability"] if m else None,
                    "evidence": m["evidence"] if m else [],
                    "note": lab.get("note"),
                }
            )
        if not y and auto_pos:
            false_confirms.append(
                {"left_id": lab["left_id"], "right_id": lab["right_id"], "probability": m["probability"], "evidence": m["evidence"], "note": lab.get("note")}
            )
    return {
        "threshold": threshold,
        "auto_confirm": dict(auto, precision=_ratio(auto["tp"], auto["tp"] + auto["fp"]), recall=_ratio(auto["tp"], auto["tp"] + auto["fn"]), f1=_f1(auto)),
        "through_review": dict(
            review, precision=_ratio(review["tp"], review["tp"] + review["fp"]), recall=_ratio(review["tp"], review["tp"] + review["fn"]), f1=_f1(review)
        ),
        "misses": misses,
        "false_confirms": false_confirms,
    }


def breakdown(matches: Dict[tuple, Dict[str, Any]], labels: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Precision per match tier or per evidence label among labeled candidates."""
    buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, "matches": 0})
    for lab in labels:
        m = matches.get((lab["left_id"], lab["right_id"]))
        if m is None:
            continue
        names = [m["match_tier"]] if key == "tier" else m["evidence"]
        for name in names:
            buckets[name]["n"] += 1
            buckets[name]["matches"] += int(lab["is_match"])
    return [{"name": k, "n": v["n"], "precision": _ratio(v["matches"], v["n"])} for k, v in sorted(buckets.items())]


def sweep(
    matches: Dict[tuple, Dict[str, Any]],
    labels: List[Dict[str, Any]],
    rules: Dict[str, Any],
    start: Optional[float] = None,
    stop: float = 1.0,
    step: float = 0.05,
) -> List[Dict[str, Any]]:
    t = start if start is not None else rules["thresholds"]["review_floor"]
    points = []
    while t <= stop + 1e-9:
        c = confusion(matches, labels, round(t, 2), rules)
        points.append(
            {
                "threshold": round(t, 2),
                "precision": c["auto_confirm"]["precision"],
                "recall": c["auto_confirm"]["recall"],
                "f1": c["auto_confirm"]["f1"],
                "false_confirms": len(c["false_confirms"]),
            }
        )
        t += step
    return points


def recommend(points: List[Dict[str, Any]], target_precision: float) -> Dict[str, Any]:
    best_f1 = max((p for p in points if p["f1"] is not None), key=lambda p: (p["f1"], -p["threshold"]), default=None)
    clearing = [p for p in points if p["precision"] is not None and p["precision"] >= target_precision]
    cheapest = min(clearing, key=lambda p: p["threshold"]) if clearing else None
    return {"best_f1": best_f1, "lowest_threshold_clearing_target": cheapest, "target_precision": target_precision}


def evaluate(store: Store, kind: str = "estate_case->parcel", rules: Optional[Dict[str, Any]] = None, target_precision: float = 0.95) -> Dict[str, Any]:
    rules = rules or load_rules()
    labels = _labels(store, kind)
    matches = {(r["left_id"], r["right_id"]): hydrate(r) for r in store.query("SELECT * FROM entity_match WHERE kind = ?", (kind,))}
    threshold = rules["thresholds"]["auto_confirm"]
    current = confusion(matches, labels, threshold, rules)
    points = sweep(matches, labels, rules)
    pairs = [(matches[(l["left_id"], l["right_id"])]["probability"], int(l["is_match"])) for l in labels if (l["left_id"], l["right_id"]) in matches]
    return {
        "kind": kind,
        "labels": len(labels),
        "positives": sum(int(l["is_match"]) for l in labels),
        "scored_labels": len(pairs),
        "current": current,
        "by_tier": breakdown(matches, labels, "tier"),
        "by_evidence": breakdown(matches, labels, "evidence"),
        "sweep": points,
        "recommendation": recommend(points, target_precision),
        "reliability": reliability(pairs),
    }


def _pct(v: Optional[float]) -> str:
    return "  n/a" if v is None else "{0:5.1f}%".format(100 * v)


def format_report(ev: Dict[str, Any]) -> str:
    cur = ev["current"]
    lines = [
        "MonitorCLT resolver evaluation -- {0}".format(ev["kind"]),
        "  {0} labels ({1} positive), {2} scored by the resolver, auto_confirm at {3}".format(
            ev["labels"], ev["positives"], ev["scored_labels"], cur["threshold"]
        ),
        "",
        "  operating point     precision  recall   f1      tp  fp  fn",
    ]
    for name, key in (("auto-confirm", "auto_confirm"), ("through review", "through_review")):
        c = cur[key]
        lines.append(
            "  {0:<18}  {1}  {2}  {3}  {4:>3} {5:>3} {6:>3}".format(name, _pct(c["precision"]), _pct(c["recall"]), _pct(c["f1"]), c["tp"], c["fp"], c["fn"])
        )
    if cur["false_confirms"]:
        lines.append("")
        lines.append("  FALSE AUTO-CONFIRMS (these would have reached export):")
        for fc in cur["false_confirms"]:
            lines.append(
                "    {0} -> {1}  p={2:.3f}  {3}  {4}".format(fc["left_id"], fc["right_id"], fc["probability"], ", ".join(fc["evidence"]), fc.get("note") or "")
            )
    if cur["misses"]:
        lines.append("")
        lines.append("  missed matches (not even in the review queue):")
        for miss in cur["misses"]:
            p = "p={0:.3f}".format(miss["probability"]) if miss["probability"] is not None else "no candidate"
            lines.append("    [{0:<11}] {1} -> {2}  {3}  {4}".format(miss["reason"], miss["left_id"], miss["right_id"], p, miss.get("note") or ""))
    lines.append("")
    lines.append("  precision by tier:")
    for row in ev["by_tier"]:
        lines.append("    {0:<30} n={1:<4} {2}".format(row["name"], row["n"], _pct(row["precision"])))
    lines.append("  precision by evidence label:")
    for row in ev["by_evidence"]:
        lines.append("    {0:<30} n={1:<4} {2}".format(row["name"], row["n"], _pct(row["precision"])))
    lines.append("")
    lines.append("  threshold sweep (auto-confirm):")
    for p in ev["sweep"]:
        lines.append(
            "    {0:.2f}  precision {1}  recall {2}  f1 {3}  false confirms {4}".format(
                p["threshold"], _pct(p["precision"]), _pct(p["recall"]), _pct(p["f1"]), p["false_confirms"]
            )
        )
    rec = ev["recommendation"]
    lines.append("")
    if rec["best_f1"]:
        lines.append("  best F1 at {0:.2f}".format(rec["best_f1"]["threshold"]))
    if rec["lowest_threshold_clearing_target"]:
        lines.append(
            "  lowest threshold clearing {0:.0%} precision: {1:.2f}  <- usually the one to ship".format(
                rec["target_precision"], rec["lowest_threshold_clearing_target"]["threshold"]
            )
        )
    else:
        lines.append("  no threshold clears {0:.0%} precision on these labels".format(rec["target_precision"]))
    rel = ev["reliability"]
    lines.append("")
    lines.append("  calibration (predicted vs observed match rate), Brier {0}:".format(rel["brier"]))
    for b in rel["bins"]:
        if b["n"]:
            lines.append("    {0}  n={1:<4} predicted {2:.2f}  observed {3:.2f}".format(b["bin"], b["n"], b["predicted"], b["observed"]))
    lines.append("")
    lines.append("  These numbers describe the labeled sample, not the county. Label the hard cases")
    lines.append("  (common surnames, remarriages, junior/senior pairs) or the harness flatters itself.")
    return "\n".join(lines)
