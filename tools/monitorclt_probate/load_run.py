#!/usr/bin/env python3
"""Load a cross-reference run into PostgreSQL (Supabase or bare).

crossref.py answers the question in memory and prints it. This puts the answer
somewhere a second person can see it, disagree with it, and leave a record of
having done so -- which is the whole reason the review queue exists. Every row of
`matches` maps 1:1 onto probate.entity_match; the sources are upserted alongside
so the views (v_estate_property, v_review_queue_detail) actually resolve.

Two rules the loader will not break:

  * A human's decision outranks the matcher. A re-run refreshes score, tier,
    evidence and flags -- the rationale should reflect the current rules -- but
    never the verdict on a row somebody has signed. (The database enforces this
    too, in the entity_match_preserve_human_review trigger; this is the same rule
    stated where the operator can read it.)
  * A confirmed match mints a person, keyed on the estate case it came from.
    Nothing else in the schema creates an identity, and an identity is never
    keyed on a name.

Requires psycopg (v3) or psycopg2 to connect. Statement building is pure and
importable without either, which is what test_load_run.py exercises.

    python3 crossref.py --estates e.jsonl --parcels p.jsonl --deeds d.jsonl --json out.json
    python3 load_run.py --run out.json --estates e.jsonl --parcels p.jsonl --deeds d.jsonl \\
                        --db "$DATABASE_URL"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crossref  # noqa: E402  (path must be set first)

LOADER_VERSION = "1.0"


class Ref:
    """A value an earlier step will produce (a RETURNING id), resolved at execute time.

    A sentinel rather than a magic string, so that a parameter carrying real
    county data can never be mistaken for a placeholder no matter what it says.
    """

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "Ref({0!r})".format(self.name)

    def __eq__(self, other):
        return isinstance(other, Ref) and other.name == self.name

    def __hash__(self):
        return hash(("Ref", self.name))


# ------------------------------------------------------------- statements ---
# Each builder returns (sql, params). Nothing is ever interpolated into a
# statement: county data contains apostrophes, ampersands and worse, and a lead
# list is not a place to discover that.


def upsert_estate_case(record):
    sql = (
        "INSERT INTO probate.estate_case ("
        " county, file_number, decedent_name, date_of_death, filing_date,"
        " case_status, personal_rep_name, pr_mailing_address, source_url)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (county, file_number) DO UPDATE SET"
        " decedent_name = EXCLUDED.decedent_name,"
        " date_of_death = EXCLUDED.date_of_death,"
        " filing_date = EXCLUDED.filing_date,"
        " case_status = EXCLUDED.case_status,"
        " personal_rep_name = EXCLUDED.personal_rep_name,"
        " pr_mailing_address = EXCLUDED.pr_mailing_address,"
        " source_url = EXCLUDED.source_url,"
        " retrieved_at = now()"
    )
    params = (
        record.get("county"),
        record.get("file_number"),
        record.get("decedent_name"),
        _date(record.get("date_of_death")),
        _date(record.get("filing_date")),
        record.get("case_status"),
        record.get("personal_rep_name"),
        record.get("pr_mailing_address"),
        record.get("source_url"),
    )
    return sql, params


def upsert_parcel(record):
    sql = (
        "INSERT INTO probate.parcel ("
        " county, pin, situs_address, owner_name, owner_mailing_address,"
        " land_use, assessed_value, deed_book, deed_page, last_sale_date, source_url)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (county, pin) DO UPDATE SET"
        " situs_address = EXCLUDED.situs_address,"
        " owner_name = EXCLUDED.owner_name,"
        " owner_mailing_address = EXCLUDED.owner_mailing_address,"
        " land_use = EXCLUDED.land_use,"
        " assessed_value = EXCLUDED.assessed_value,"
        " deed_book = EXCLUDED.deed_book,"
        " deed_page = EXCLUDED.deed_page,"
        " last_sale_date = EXCLUDED.last_sale_date,"
        " source_url = EXCLUDED.source_url,"
        " retrieved_at = now()"
    )
    params = (
        record.get("county"),
        record.get("pin"),
        record.get("situs_address"),
        record.get("owner_name"),
        record.get("owner_mailing_address"),
        record.get("land_use"),
        record.get("assessed_value"),
        record.get("deed_book"),
        record.get("deed_page"),
        _date(record.get("last_sale_date")),
        record.get("source_url"),
    )
    return sql, params


def upsert_deed(record):
    # The UNIQUE is (county, book, page, instrument_number) and PostgreSQL's
    # ON CONFLICT will not fire on a key containing NULL, so a deed with no
    # instrument number would insert a duplicate on every run. Coalesce to '' to
    # keep the key total.
    sql = (
        "INSERT INTO probate.deed ("
        " county, instrument_number, book, page, recorded_date, instrument_type,"
        " grantor_name, grantee_name, parcel_pin, source_url)"
        " VALUES (%s, coalesce(%s, ''), coalesce(%s, ''), coalesce(%s, ''), %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (county, book, page, instrument_number) DO UPDATE SET"
        " recorded_date = EXCLUDED.recorded_date,"
        " instrument_type = EXCLUDED.instrument_type,"
        " grantor_name = EXCLUDED.grantor_name,"
        " grantee_name = EXCLUDED.grantee_name,"
        " parcel_pin = EXCLUDED.parcel_pin,"
        " source_url = EXCLUDED.source_url,"
        " retrieved_at = now()"
    )
    params = (
        record.get("county"),
        record.get("instrument_number"),
        record.get("book"),
        record.get("page"),
        _date(record.get("recorded_date")),
        record.get("instrument_type"),
        record.get("grantor_name"),
        record.get("grantee_name"),
        record.get("parcel_pin"),
        record.get("source_url"),
    )
    return sql, params


def insert_match_run(run):
    """Open a run. params carries the counts, so a row's provenance is legible later."""
    sql = (
        "INSERT INTO probate.match_run (tool_version, rules_version, params)"
        " VALUES (%s, %s, %s::jsonb) RETURNING id"
    )
    params = (
        run.get("tool_version"),
        run.get("rules_version"),
        json.dumps(
            {
                "estate_cases": run.get("estate_cases"),
                "parcels": run.get("parcels"),
                "deeds": run.get("deeds"),
                "loader_version": LOADER_VERSION,
            },
            sort_keys=True,
        ),
    )
    return sql, params


def upsert_person(left_id, display_name, normalized_name):
    """Mint (or find) the identity behind a confirmed match, keyed on its estate case."""
    sql = (
        "INSERT INTO probate.person (source_ref, normalized_name, display_name, is_organization)"
        " VALUES (%s, %s, %s, false)"
        " ON CONFLICT (source_ref) DO UPDATE SET"
        " normalized_name = EXCLUDED.normalized_name,"
        " display_name = EXCLUDED.display_name"
        " RETURNING id"
    )
    return sql, ("estate_case:{0}".format(left_id), normalized_name, display_name)


def upsert_person_alias(person_id, alias_normalized, source):
    sql = (
        "INSERT INTO probate.person_alias (person_id, alias_normalized, source)"
        " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
    )
    return sql, (person_id, alias_normalized, source)


def upsert_match(run_id, link, person_id=None):
    """One candidate assertion.

    status is written as the matcher decided it. On a row a human has already
    reviewed, the database trigger puts their verdict back -- so the effect of a
    re-run is that the rationale moves and the decision does not.
    """
    sql = (
        "INSERT INTO probate.entity_match ("
        " run_id, left_source, left_id, right_source, right_id, person_id,"
        " match_tier, score, evidence, flags, status)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)"
        " ON CONFLICT (left_source, left_id, right_source, right_id) DO UPDATE SET"
        " run_id = EXCLUDED.run_id,"
        " match_tier = EXCLUDED.match_tier,"
        " score = EXCLUDED.score,"
        " evidence = EXCLUDED.evidence,"
        " flags = EXCLUDED.flags,"
        " status = EXCLUDED.status,"
        " person_id = coalesce(probate.entity_match.person_id, EXCLUDED.person_id)"
        " RETURNING id"
    )
    params = (
        run_id,
        link["left_source"],
        link["left_id"],
        link["right_source"],
        link["right_id"],
        person_id,
        link["match_tier"],
        link["score"],
        json.dumps(link.get("evidence", [])),
        json.dumps(link.get("flags", [])),
        link["status"],
    )
    return sql, params


def _date(value):
    """Trim an ISO timestamp to a date; empty string is not a date, it is a NULL."""
    if not value:
        return None
    return str(value)[:10]


# ------------------------------------------------------------------ plan ----


def plan(result, sources=None, rules=None):
    """Everything the load will do, as ordered (label, sql, params) steps.

    Pure: no connection, no side effects. `insert_match_run` and `upsert_person`
    return ids the later steps need, so those steps carry a `returns` label and
    the executor threads the value through -- which is also what makes the whole
    plan inspectable before anything is written.
    """
    sources = sources or {}
    rules = rules or crossref.load_rules(None)
    estate_fmt = rules.get("name_formats", {}).get("estate_case", "first_last")
    parcel_fmt = rules.get("name_formats", {}).get("parcel", "last_first")

    steps = []
    for record in sources.get("estates", []):
        steps.append(("estate_case", upsert_estate_case(record), None))
    for record in sources.get("parcels", []):
        steps.append(("parcel", upsert_parcel(record), None))
    for record in sources.get("deeds", []):
        steps.append(("deed", upsert_deed(record), None))

    steps.append(("match_run", insert_match_run(result["run"]), "run_id"))
    run_ref = Ref("run_id")

    minted = set()
    for link in result["matches"]:
        person_ref = None
        if link["status"] == "confirmed":
            key = "person:{0}".format(link["left_id"])
            person_ref = Ref(key)
            if key not in minted:
                minted.add(key)
                decedent = crossref.parse_name(link.get("decedent_name") or "", estate_fmt, rules)
                steps.append(
                    (
                        "person",
                        upsert_person(
                            link["left_id"], link.get("decedent_name") or "", decedent["normalized"]
                        ),
                        key,
                    )
                )
                steps.append(
                    ("person_alias", upsert_person_alias(person_ref, decedent["normalized"], "estate_case"), None)
                )
                # The assessor's owner string is a second spelling of the same
                # person; keeping it as an alias is how the next run's near-miss
                # becomes explainable instead of mysterious.
                owner = crossref.parse_name(link.get("owner_name") or "", parcel_fmt, rules)
                if owner["normalized"] and owner["normalized"] != decedent["normalized"]:
                    steps.append(
                        ("person_alias", upsert_person_alias(person_ref, owner["normalized"], "parcel"), None)
                    )
        steps.append(("entity_match", upsert_match(run_ref, link, person_ref), None))

    return steps


def summarize(result):
    counts = {"confirmed": 0, "pending": 0, "rejected": 0}
    for link in result["matches"]:
        counts[link["status"]] = counts.get(link["status"], 0) + 1
    return {
        "tool_version": result["run"].get("tool_version"),
        "rules_version": result["run"].get("rules_version"),
        "matches": len(result["matches"]),
        "confirmed": counts["confirmed"],
        "pending": counts["pending"],
        "rejected": counts["rejected"],
        "estates_with_property": sum(1 for row in result["estates"] if row["has_real_property"]),
        "estates": len(result["estates"]),
        "input_gaps": len(result.get("unmatched_estate_parcels", [])),
    }


# -------------------------------------------------------------- execution ---


def connect(dsn):
    """psycopg 3 if it is there, psycopg2 if it is not, a clear error if neither."""
    try:
        import psycopg  # noqa: F401  (imported for its side effect: availability)

        return psycopg.connect(dsn)
    except ImportError:
        pass
    try:
        import psycopg2

        return psycopg2.connect(dsn)
    except ImportError:
        raise SystemExit(
            "no PostgreSQL driver: pip install 'psycopg[binary]' (or psycopg2-binary)"
        )


def execute(connection, steps):
    """Run the plan in one transaction, threading returned ids into later steps.

    All or nothing: a half-loaded run is worse than no run, because the review
    queue would look shorter than it is.
    """
    bindings = {}
    written = {}
    cursor = connection.cursor()
    try:
        for label, (sql, params), returns in steps:
            resolved = tuple(bindings[p.name] if isinstance(p, Ref) else p for p in params)
            cursor.execute(sql, resolved)
            if returns:
                row = cursor.fetchone()
                bindings[returns] = row[0]
            written[label] = written.get(label, 0) + 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="crossref.py --json output")
    parser.add_argument("--estates", help="estate case records, upserted alongside the run")
    parser.add_argument("--parcels", help="parcel/assessor records")
    parser.add_argument("--deeds", help="recorded deed records")
    parser.add_argument("--rules", help="matching rules (default: match_rules.json beside this script)")
    parser.add_argument("--db", help="PostgreSQL URL (default: $DATABASE_URL)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be written, per table, and connect to nothing",
    )
    args = parser.parse_args(argv)

    with open(args.run, encoding="utf-8") as f:
        result = json.load(f)

    sources = {
        "estates": crossref.load_records(args.estates) if args.estates else [],
        "parcels": crossref.load_records(args.parcels) if args.parcels else [],
        "deeds": crossref.load_records(args.deeds) if args.deeds else [],
    }
    steps = plan(result, sources, crossref.load_rules(args.rules))
    counts = summarize(result)

    if args.dry_run:
        # Deliberately no row contents: this is real people's addresses, and a
        # dry run tends to end up in a terminal log.
        per_table = {}
        for label, _statement, _returns in steps:
            per_table[label] = per_table.get(label, 0) + 1
        print("would write {0} statements:".format(len(steps)))
        for label in sorted(per_table):
            print("    {0:<14} {1}".format(label, per_table[label]))
        print("run: {0}".format(json.dumps(counts, sort_keys=True)))
        return 0

    dsn = args.db or os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("--db or $DATABASE_URL is required (or use --dry-run)")

    connection = connect(dsn)
    try:
        written = execute(connection, steps)
    finally:
        connection.close()

    print("loaded {0}".format(json.dumps(counts, sort_keys=True)))
    for label in sorted(written):
        print("    {0:<14} {1}".format(label, written[label]))
    print(
        "{0} pending candidate(s) are now in probate.v_review_queue_detail and are "
        "not leads until a human says so.".format(counts["pending"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
