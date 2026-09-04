#!/usr/bin/env python3
"""Run the cross-reference on one day's pull and record it: the loader.

Each invocation is one dated observation of the county records. It runs
crossref.py on the inputs, writes the run into the store (store.py), compares it
to the previous run, and prints the delta -- new leads, status changes, and
every parcel detected leaving its estate. Run it once per pull; `report.py` then
answers "how many new targets per day" and "which leads have since sold".

    python3 load_run.py --db monitorclt.sqlite --as-of 2026-09-01 \
        --estates pull/estate_cases.jsonl --parcels pull/parcels.jsonl --deeds pull/deeds.jsonl

--as-of is the date the inputs describe (the pull date), not today: it is what
the daily cohorts are keyed on, so a backfill of historical pulls dated
correctly produces correct history. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import store  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="SQLite store path (created if missing)")
    parser.add_argument("--as-of", dest="as_of", help="date the inputs describe, YYYY-MM-DD (default: today)")
    parser.add_argument("--estates", required=True)
    parser.add_argument("--parcels", required=True)
    parser.add_argument("--deeds")
    parser.add_argument("--rules")
    parser.add_argument("--json", dest="json_out", help="also write the crossref result JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    rules = crossref.load_rules(args.rules)
    estates = crossref.load_records(args.estates)
    parcels = crossref.load_records(args.parcels)
    deeds = crossref.load_records(args.deeds) if args.deeds else []

    result = crossref.crossref(estates, parcels, deeds, rules)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)

    conn = store.connect(args.db)
    params = {"estates": os.path.basename(args.estates), "parcels": os.path.basename(args.parcels), "deeds": os.path.basename(args.deeds or "")}
    _, delta = store.load_run(conn, result, estates, parcels, deeds, rules, as_of=args.as_of, params=params)
    conn.close()
    if not args.quiet:
        print(store.format_delta(delta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
