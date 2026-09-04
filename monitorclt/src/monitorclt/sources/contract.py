"""Contract tests for connectors: parse the recorded fixture, compare to the golden.

When a county changes its markup, this is what goes red before the pipeline quietly
starts returning zero rows.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .base import Connector
from .transport import FixtureTransport


def run_connector_offline(connector: Connector, transport: FixtureTransport) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for fetched in connector.fetch(transport, None):
        records.extend(connector.clean(r) for r in connector.parse(fetched.body, fetched.content_type))
    return records


def validate(connector: Connector, records: List[Dict[str, Any]]) -> List[str]:
    """Problems that make records unusable: missing keys, missing required fields, duplicate keys."""
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for i, rec in enumerate(records):
        missing = connector.spec.missing_required(rec)
        if missing:
            problems.append("row {0}: missing required {1}".format(i, missing))
        key = connector.spec.natural_key(rec)
        if not key.strip("|"):
            problems.append("row {0}: empty natural key".format(i))
        elif key in seen:
            problems.append("row {0}: duplicate key {1!r} (also row {2})".format(i, key, seen[key]))
        seen.setdefault(key, i)
    return problems


def check(connector: Connector, fixture_root: str, golden_path: Optional[str] = None, write_golden: bool = False) -> Dict[str, Any]:
    transport = FixtureTransport(fixture_root)
    records = run_connector_offline(connector, transport)
    problems = validate(connector, records)
    result: Dict[str, Any] = {"connector": connector.name, "county": connector.county, "records": len(records), "problems": problems, "golden": None}
    if golden_path:
        if write_golden or not os.path.exists(golden_path):
            with open(golden_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, sort_keys=True)
            result["golden"] = "written"
        else:
            with open(golden_path, encoding="utf-8") as f:
                golden = json.load(f)
            if golden != records:
                result["golden"] = "mismatch"
                problems.append("parsed output differs from golden {0}".format(os.path.basename(golden_path)))
            else:
                result["golden"] = "ok"
    result["ok"] = not problems
    return result
