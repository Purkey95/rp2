"""Pin the five governance rules against fixtures — the anti-fabrication guarantees.

Run: python3 test_sourcing.py
"""

import json
import os

from registry import load_registry, validate_entry
from adapters import get_adapter
from adapters.base import normalize_rows

HERE = os.path.dirname(os.path.abspath(__file__))


def load_entry(rid):
    for s in load_registry(os.path.join(HERE, "jurisdictions.sample.json")):
        if s["registry_id"] == rid:
            return s
    raise KeyError(rid)


def rows(entry, fixture):
    with open(os.path.join(HERE, "fixtures", fixture), encoding="utf-8") as f:
        data = json.load(f)
    adapter = get_adapter(entry["platform"])
    if entry["platform"] == "arcgis":
        return [adapter._flatten(feat) for feat in data["features"]]
    return list(data)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # ---- ArcGIS permits: 3 emit, 1 unmapped-status, 1 blank-status ----
    permits = load_entry("meck-building-permits")
    signals, quarantines, report = normalize_rows(permits, rows(permits, "arcgis_permits.json"))

    ok &= check("permits emitted", report.emitted, 3)
    ok &= check("permits data_quality", report.data_quality, "pass")
    ok &= check("permits unmapped surfaced",
                "On Hold Pending Appeal" in report.unmapped_statuses, True)
    ok &= check("permits blank-status excluded", report.excluded_blank_status, 1)

    # Rule 1: every emitted signal carries a source_url.
    ok &= check("all signals have source_url", all(s.source_url for s in signals), True)
    # Rule 1: template fills when the record_url column is empty (row 2).
    dem = next(s for s in signals if s.signal_type == "demolition_permit")
    ok &= check("template-built url", dem.source_url,
                "https://permits.mecknc.gov/permit/DEM-2026-000733")
    # Rule 2: classification only from type_map.
    ok &= check("classified from type_map",
                sorted({s.signal_type for s in signals}),
                ["building_permit", "demolition_permit"])
    # Rule 3: point geometry -> precise; all sample permits carry geometry.
    ok &= check("geo precision point", all(s.geo_precision == "point" for s in signals), True)
    # confidence verified (record precision) for all three.
    ok &= check("confidence verified", all(s.confidence == "verified" for s in signals), True)

    # ---- Socrata code violations: quarantine the row with no resolvable url ----
    cv = load_entry("charlotte-code-violations")
    csig, cq, creport = normalize_rows(cv, rows(cv, "socrata_code_violations.json"))
    ok &= check("code-violations emitted", creport.emitted, 2)
    ok &= check("no_source_url quarantine",
                any(q.reason == "no_source_url" for q in cq), True)
    # Rule 2 default_type applies when type maps but let's confirm mapped type wins.
    ok &= check("vacancy classified",
                any(s.signal_type == "vacancy" for s in csig), True)

    # ---- Idempotency: same input -> same signal_ids ----
    s2, _, _ = normalize_rows(permits, rows(permits, "arcgis_permits.json"))
    ok &= check("stable ids (idempotent)",
                [s.signal_id for s in signals] == [s.signal_id for s in s2], True)

    # ---- Registry validation catches an unsourceable entry ----
    bad = {"registry_id": "x", "platform": "socrata", "source_name": "X",
           "domain": "d", "dataset_id": "i",
           "column_map": {"status": "s"}, "status_to_bucket": {"Open": "active"}}
    ok &= check("validation flags no-url entry",
                any("nothing can be sourced" in p for p in validate_entry(bad)), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
