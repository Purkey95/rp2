#!/usr/bin/env python3
"""Run a source adapter and emit sourced signals + a run report.

Two modes:
  --fixture PATH   read rows from a saved JSON fixture (offline, deterministic).
                   ArcGIS fixtures are the raw /query response; Socrata fixtures
                   are the raw row array. This is how the tests and a dry-run work.
  (live)           without --fixture, the adapter fetches from the real endpoint
                   in the registry entry via urllib.

Output per source:
  signals.jsonl        one JSON object per emitted (sourced) signal
  quarantine.jsonl     every dropped row + the reason (never silently discarded)
  report.json          MonitorCLT-style run report incl. data_quality gate

Usage:
  python3 run_source.py --registry jurisdictions.sample.json \\
      --source meck-building-permits --fixture fixtures/arcgis_permits.json --outdir out
"""

import argparse
import json
import os
import urllib.request
from dataclasses import asdict

from registry import load_registry
from adapters import get_adapter
from adapters.base import normalize_rows


def live_fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MonitorCLT-sourcing/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def rows_from_fixture(entry, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Reuse each adapter's own shaping so the fixture path matches the live path.
    adapter = get_adapter(entry["platform"])
    if entry["platform"] == "arcgis":
        return [adapter._flatten(feat) for feat in data.get("features", [])]
    return list(data)  # socrata: already a row array


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Run a MonitorCLT source adapter")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--source", required=True, help="registry_id to run")
    ap.add_argument("--fixture", help="JSON fixture path (offline mode)")
    ap.add_argument("--zip", help="restrict to one ZIP")
    ap.add_argument("--limit", type=int, help="cap records fetched (smoke-testing big feeds)")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    sources = load_registry(args.registry)
    entry = next((s for s in sources if s["registry_id"] == args.source), None)
    if not entry:
        raise SystemExit(f"source '{args.source}' not in registry")

    if args.fixture:
        rows = rows_from_fixture(entry, args.fixture)
        signals, quarantines, report = normalize_rows(entry, rows)
    else:
        adapter = get_adapter(entry["platform"], fetch_json=live_fetch_json)
        if args.limit:
            import itertools
            rows = list(itertools.islice(adapter.fetch_rows(entry, args.zip), args.limit))
            signals, quarantines, report = normalize_rows(entry, rows)
        else:
            signals, quarantines, report = adapter.run(entry, args.zip)

    os.makedirs(args.outdir, exist_ok=True)
    write_jsonl(os.path.join(args.outdir, "signals.jsonl"), [s.as_row() for s in signals])
    write_jsonl(os.path.join(args.outdir, "quarantine.jsonl"),
                [{"reason": q.reason, "detail": q.detail} for q in quarantines])
    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report.summary(), f, indent=2)

    r = report.summary()
    print(f"Source        : {r['source']}")
    print(f"Data quality  : {r['data_quality'].upper()}")
    print(f"Considered    : {r['considered']}")
    print(f"Emitted       : {r['emitted']}  (by type: {r['by_type']})")
    print(f"Quarantined   : {r['quarantined']} (no source url / error)")
    print(f"Excluded      : {r['excluded_blank_status']} blank status, "
          f"{r['excluded_unmapped_status']} unmapped status")
    if r["unmapped_statuses"]:
        print(f"  UNMAPPED (add to registry): {r['unmapped_statuses']}")
    print(f"Wrote         : {args.outdir}/signals.jsonl, quarantine.jsonl, report.json")


if __name__ == "__main__":
    main()
