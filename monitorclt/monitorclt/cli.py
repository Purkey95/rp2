"""Command line interface for the parcel index.

    python -m monitorclt.cli load     --db parcels.db --limit 5000
    python -m monitorclt.cli stats    --db parcels.db
    python -m monitorclt.cli address  --db parcels.db "210 N Church St Unit 1104"
    python -m monitorclt.cli person   --db parcels.db "Walter H Conrad" --city CHARLOTTE
    python -m monitorclt.cli decedents --db parcels.db
    python -m monitorclt.cli load-sales --db parcels.db
    python -m monitorclt.cli backtest   --db parcels.db --as-of 2022-01-01 --horizon-years 2
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import List, Optional, Sequence

from datetime import date

from .backtest.harness import run_backtest
from .backtest.report import format_report
from .propensity.dataset import build_examples
from .propensity.model import PropensityModel
from .propensity.validate import format_validation, validate
from .parcel.enrich import ParcelIndex, summarize_candidates
from .parcel.loader import load_parcels
from .parcel.store import ParcelStore
from .sales.loader import load_sales


def _print_rows(rows: Sequence[sqlite3.Row], columns: Sequence[str]) -> None:
    if not rows:
        print("no results")
        return
    for row in rows:
        print("  ".join(str(row[column] if row[column] is not None else "-") for column in columns))
    print("")
    print(str(len(rows)) + " row(s)")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="monitorclt", description=__doc__)
    parser.add_argument("--db", default="parcels.db", help="path to the SQLite parcel index")
    subparsers = parser.add_subparsers(dest="command", required=True)

    load = subparsers.add_parser("load", help="fetch parcels into the index")
    load.add_argument("--limit", type=int, default=None, help="stop after N records")
    load.add_argument("--where", default="1=1", help="ArcGIS where clause")

    load_sales_parser = subparsers.add_parser("load-sales", help="fetch sales history")
    load_sales_parser.add_argument("--limit", type=int, default=None)
    load_sales_parser.add_argument("--where", default="saledate >= DATE '1980-01-01'")

    backtest = subparsers.add_parser("backtest", help="measure signals against outcomes")
    backtest.add_argument("--as-of", required=True, help="reconstruction date, YYYY-MM-DD")
    backtest.add_argument("--horizon-years", type=float, default=2.0)
    backtest.add_argument("--outcome", action="append", default=None,
                          help="restrict to one outcome; repeatable")

    propensity = subparsers.add_parser(
        "propensity", help="fit a propensity model and validate it out of time"
    )
    propensity.add_argument("--train-as-of", default="2016-01-01")
    propensity.add_argument("--test-as-of", default="2022-01-01")
    propensity.add_argument("--horizon-years", type=float, default=2.0)

    subparsers.add_parser("stats", help="summarize the loaded index")

    address = subparsers.add_parser("address", help="look up parcels at an address")
    address.add_argument("address")

    parcel = subparsers.add_parser("parcel", help="look up one parcel by camapid")
    parcel.add_argument("parcel_key")

    person = subparsers.add_parser("person", help="find candidate parcels for a person")
    person.add_argument("name")
    person.add_argument("--city", default=None)
    person.add_argument("--include-weak", action="store_true")

    subparsers.add_parser("decedents", help="parcels whose owner string marks a decedent")

    absentee = subparsers.add_parser("absentee", help="absentee-owned parcels by value")
    absentee.add_argument("--out-of-state", action="store_true")
    absentee.add_argument("--limit", type=int, default=25)

    args = parser.parse_args(argv)

    with ParcelStore(args.db) as store:
        index = ParcelIndex(store)

        if args.command == "load":
            written = load_parcels(
                store,
                where=args.where,
                limit=args.limit,
                progress=lambda n: print("  fetched " + str(n) + " ...", file=sys.stderr),
            )
            print("loaded " + str(written) + " parcels into " + args.db)
        elif args.command == "load-sales":
            written = load_sales(
                store,
                where=args.where,
                limit=args.limit,
                progress=lambda n: print("  fetched " + str(n) + " ...", file=sys.stderr),
            )
            print("loaded " + str(written) + " sales into " + args.db)
        elif args.command == "backtest":
            as_of = date.fromisoformat(args.as_of)
            horizon_end = date.fromordinal(
                as_of.toordinal() + int(round(args.horizon_years * 365.25))
            )
            report = run_backtest(store, as_of=as_of, horizon_end=horizon_end)
            print(format_report(report, outcomes=args.outcome))
        elif args.command == "propensity":
            def window(start_text):
                start = date.fromisoformat(start_text)
                return start, date.fromordinal(
                    start.toordinal() + int(round(args.horizon_years * 365.25))
                )

            train_as_of, train_end = window(args.train_as_of)
            test_as_of, test_end = window(args.test_as_of)
            if train_end > test_as_of:
                print(
                    "error: train window ("
                    + train_as_of.isoformat() + " to " + train_end.isoformat()
                    + ") overlaps the test window starting " + test_as_of.isoformat()
                    + "; overlapping windows measure memorisation, not prediction",
                    file=sys.stderr,
                )
                return 2
            model = PropensityModel.fit(
                build_examples(store, as_of=train_as_of, horizon_end=train_end)
            )
            report = validate(
                model, build_examples(store, as_of=test_as_of, horizon_end=test_end)
            )
            print(
                "trained "
                + train_as_of.isoformat() + " -> " + train_end.isoformat()
                + "   tested " + test_as_of.isoformat() + " -> " + test_end.isoformat()
            )
            print("")
            print(format_validation(report, model))
        elif args.command == "stats":
            print(json.dumps(store.stats(), indent=2, sort_keys=True))
        elif args.command == "address":
            _print_rows(
                index.by_address(args.address),
                ("parcel_key", "owner_raw", "situs_raw", "owner_type", "total_value"),
            )
        elif args.command == "parcel":
            row = index.by_parcel_key(args.parcel_key)
            if row is None:
                print("not found")
                return 1
            for key in row.keys():
                print("%-20s %s" % (key, row[key]))
        elif args.command == "person":
            print(
                summarize_candidates(
                    index.candidates_for_person(
                        args.name, city=args.city, include_weak=args.include_weak
                    )
                )
            )
        elif args.command == "decedents":
            _print_rows(
                index.decedent_marked(),
                ("parcel_key", "owner_raw", "care_of", "situs_raw", "total_value"),
            )
        elif args.command == "absentee":
            _print_rows(
                index.absentee_owners(out_of_state_only=args.out_of_state, limit=args.limit),
                ("parcel_key", "owner_raw", "mail_city", "mail_state", "situs_raw", "total_value"),
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
