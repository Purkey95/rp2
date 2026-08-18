"""Jurisdiction registry loader + validation.

The registry is DATA: adding a county/source is appending one entry, not editing
code. This module loads it and validates that each entry has the keys the
adapters and the normalizer require, so a malformed entry fails loudly at load
rather than silently emitting nothing at 3am.
"""

import json

REQUIRED = ("registry_id", "platform", "source_name", "column_map")
PLATFORM_KEYS = {
    "arcgis": ("service_url",),
    "socrata": ("domain", "dataset_id"),
}


def validate_entry(entry):
    problems = []
    for k in REQUIRED:
        if not entry.get(k):
            problems.append(f"missing '{k}'")
    plat = entry.get("platform")
    for k in PLATFORM_KEYS.get(plat, ()):
        if not entry.get(k):
            problems.append(f"{plat} entry missing '{k}'")
    cmap = entry.get("column_map", {})
    # A source needs EITHER a status column (with a status_to_bucket map) OR a
    # default_bucket (statusless list, e.g. a tax-sale roster).
    if cmap.get("status"):
        if not entry.get("status_to_bucket"):
            problems.append("column_map.status is set but status_to_bucket is missing/empty")
    elif not entry.get("default_bucket"):
        problems.append("need column_map.status (+status_to_bucket) or default_bucket")
    # Anti-fabrication: an entry must be able to produce a URL some way, or every
    # row it yields will quarantine — catch that at load, not per-row at runtime.
    if not (cmap.get("record_url") or entry.get("record_url_template") or entry.get("dataset_url")):
        problems.append("no record_url column, record_url_template, or dataset_url — nothing can be sourced")
    return problems


def load_registry(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    sources = doc.get("sources", [])
    errors = {}
    for e in sources:
        probs = validate_entry(e)
        if probs:
            errors[e.get("registry_id", "<no id>")] = probs
    if errors:
        raise ValueError(f"registry validation failed: {json.dumps(errors, indent=2)}")
    return sources
