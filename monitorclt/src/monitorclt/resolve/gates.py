"""Hard gates: rules no probability may override.

The model tunes the gray zone. These decide what is *allowed* to auto-confirm at
all, and they are the part of the system that a compliance reviewer reads first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def evaluate(features: Dict[str, float], rules: Dict[str, Any]) -> Dict[str, bool]:
    cfg = rules.get("gates", {})
    corroborating = rules.get("corroborating", [])
    conflicts = rules.get("hard_conflicts", [])
    gates: Dict[str, bool] = {}
    if cfg.get("same_county", True):
        gates["same_county"] = not features.get("county_mismatch")
    if cfg.get("not_organization", True):
        gates["not_organization"] = not features.get("organization_candidate")
    if cfg.get("corroboration_required", True):
        gates["corroboration_required"] = any(features.get(k) for k in corroborating)
    if cfg.get("no_hard_conflict", True):
        gates["no_hard_conflict"] = not any(features.get(k) for k in conflicts)
    if cfg.get("no_weak_name_only", True):
        weak = features.get("name_initial_only") or features.get("name_phonetic_match")
        strong = sum(1 for k in corroborating if features.get(k))
        gates["no_weak_name_only"] = not weak or strong >= 2
    return gates


def disposition(probability: float, gates: Dict[str, bool], features: Dict[str, float], rules: Dict[str, Any]) -> Tuple[str, List[str]]:
    """confirmed / pending / rejected, plus the reasons a reviewer needs to see."""
    t = rules["thresholds"]
    flags: List[str] = []
    if probability < t["review_floor"]:
        return "rejected", flags
    failed = [g for g, ok in gates.items() if not ok]
    if probability >= t["auto_confirm"] and not failed:
        return "confirmed", flags
    if "corroboration_required" in failed:
        flags.append("name_only_needs_human_review")
    for g in failed:
        if g != "corroboration_required":
            flags.append("gate_failed:" + g)
    return "pending", flags


def tier(features: Dict[str, float], rules: Dict[str, Any]) -> str:
    for name in rules.get("tier_priority", []):
        if features.get(name):
            return name
    return "name_full_exact" if features.get("name_full_exact") else "name_first_last_only"
