#!/usr/bin/env python3
"""Turn a crossref.py run into SQL for psql. The loader agents/skills/load-run.md specifies.

Pure stdlib, so it cannot talk to PostgreSQL itself: it writes one transaction
of SQL and you pipe it through psql:

    python3 load_run.py --run runs/x.json --estates ... --parcels ... --deeds ... \\
                        --entities ... > load.sql
    psql "$DSN" -v ON_ERROR_STOP=1 -1 -f load.sql

What it gets right that a "straight insert" would not:

  * probate.entity_match requires a confirmed row to name a person or a
    reviewer. A rule run is an auditable reviewer: confirmed rows get
    reviewer = 'rules@<rules_version>/run:<match_run.id>' and a null
    reviewed_at, so a machine confirm stays distinguishable from a human one
    and no probate.person row is invented.
  * entity_match is UNIQUE on the (estate, parcel) pair, not on run_id, so a
    re-run collides with rows a human has already reviewed. A human decision
    always wins: a reviewed row takes fresh score/tier/evidence/flags/via_*
    and keeps its status, reviewer, reviewed_at, review_note and person_id.
  * Rejected rows load too. A re-run that promotes a rejected pair to pending
    is exactly what the review queue exists to carry.
  * Source rows are upserted on their natural keys. Sources are never mutated
    by matching; refreshing them from their own source is not matching.

The closing SELECT lists every pair where the matcher's fresh status differs
from a preserved human status -- the most interesting output of a re-run.
"""

from __future__ import annotations

import argparse
import json
import sys

from crossref import load_records

ESTATE_COLUMNS = (
    "county", "file_number", "decedent_name", "date_of_death", "filing_date",
    "case_status", "personal_rep_name", "pr_mailing_address", "source_url", "retrieved_at",
)
PARCEL_COLUMNS = (
    "county", "pin", "situs_address", "owner_name", "owner_mailing_address", "land_use",
    "assessed_value", "deed_book", "deed_page", "last_sale_date", "source_url", "retrieved_at",
)
DEED_COLUMNS = (
    "county", "instrument_number", "book", "page", "recorded_date", "instrument_type",
    "grantor_name", "grantee_name", "parcel_pin", "source_url", "retrieved_at",
)
ENTITY_COLUMNS = (
    "sos_id", "entity_name", "entity_type", "status", "domestic", "formation_date",
    "principal_office_address", "mailing_address", "registered_agent_name",
    "registered_agent_address", "source_url", "retrieved_at",
)
MATCH_COLUMNS = (
    "left_source", "left_id", "right_source", "right_id", "match_tier", "score",
    "evidence", "flags", "status", "via_source", "via_id",
)
JSONB_COLUMNS = {"evidence", "flags"}
PRESERVED_BY_REVIEW = ("status", "reviewer")  # plus reviewed_at / review_note / person_id, never touched


# ------------------------------------------------------------------ sql ----


def sql_literal(value, jsonb=False):
    """A safe SQL literal. Strings are quoted with '' doubling; never interpolated raw."""
    if value is None:
        return "NULL"
    if jsonb:
        return "{0}::jsonb".format(sql_literal(json.dumps(value, sort_keys=True)))
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return "E'{0}'".format(text) if "\\" in text else "'{0}'".format(text)


def _values(record, columns, defaults=()):
    parts = []
    for column in columns:
        value = record.get(column)
        if value is None and column in defaults:
            parts.append("DEFAULT")
        else:
            parts.append(sql_literal(value, jsonb=column in JSONB_COLUMNS))
    return "({0})".format(", ".join(parts))


def _upsert(table, columns, records, conflict, defaults=("retrieved_at",)):
    """INSERT ... ON CONFLICT (natural key) DO UPDATE every other column."""
    if not records:
        return []
    updatable = [c for c in columns if c not in conflict]
    lines = []
    for record in records:
        lines.append(
            "INSERT INTO {0} ({1})\n  VALUES {2}\n  ON CONFLICT ({3}) DO UPDATE SET {4};".format(
                table,
                ", ".join(columns),
                _values(record, columns, defaults),
                ", ".join(conflict),
                ", ".join("{0} = EXCLUDED.{0}".format(c) for c in updatable),
            )
        )
    return lines


def _match_row(link, rules_version):
    """One entity_match upsert, human decisions preserved."""
    values = []
    for column in MATCH_COLUMNS:
        values.append(sql_literal(link.get(column), jsonb=column in JSONB_COLUMNS))
    reviewer = (
        "'rules@' || {0} || '/run:' || (SELECT id FROM _run)::text".format(sql_literal(rules_version))
        if link["status"] == "confirmed"
        else "NULL"
    )
    refreshed = ["run_id", "match_tier", "score", "evidence", "flags", "via_source", "via_id"]
    sets = ["{0} = EXCLUDED.{0}".format(c) for c in refreshed]
    for column in PRESERVED_BY_REVIEW:
        sets.append(
            "{0} = CASE WHEN probate.entity_match.reviewed_at IS NULL "
            "THEN EXCLUDED.{0} ELSE probate.entity_match.{0} END".format(column)
        )
    return (
        "INSERT INTO probate.entity_match (run_id, {0}, reviewer)\n"
        "  VALUES ((SELECT id FROM _run), {1}, {2})\n"
        "  ON CONFLICT (left_source, left_id, right_source, right_id) DO UPDATE SET\n"
        "    {3};".format(", ".join(MATCH_COLUMNS), ", ".join(values), reviewer, ",\n    ".join(sets))
    )


def render(result, estates=(), parcels=(), deeds=(), entities=(), params=None):
    """The whole load as one transaction of SQL text."""
    run = result["run"]
    rules_version = run.get("rules_version") or "unknown"
    params = dict(run, **(params or {}))
    lines = [
        "-- generated by load_run.py; one transaction, psql -1 -v ON_ERROR_STOP=1",
        "BEGIN;",
        "",
        "CREATE TEMP TABLE _run (id bigint) ON COMMIT DROP;",
        "WITH r AS (",
        "  INSERT INTO probate.match_run (tool_version, rules_version, params)",
        "  VALUES ({0}, {1}, {2})".format(
            sql_literal(run.get("tool_version") or "unknown"),
            sql_literal(rules_version),
            sql_literal(params, jsonb=True),
        ),
        "  RETURNING id",
        ")",
        "INSERT INTO _run SELECT id FROM r;",
        "",
    ]

    # Sources first: a match must never point at a record that is not there.
    lines.append("-- sources, upserted on their natural keys")
    lines.extend(_upsert("probate.estate_case", ESTATE_COLUMNS, estates, ("county", "file_number")))
    lines.extend(_upsert("probate.parcel", PARCEL_COLUMNS, parcels, ("county", "pin")))
    lines.extend(
        _upsert("probate.deed", DEED_COLUMNS, deeds, ("county", "book", "page", "instrument_number"))
    )
    lines.extend(_upsert("probate.business_entity", ENTITY_COLUMNS, entities, ("sos_id",)))
    for entity in entities:
        for official in entity.get("officials") or []:
            lines.append(
                "INSERT INTO probate.entity_official (entity_id, person_name, title, source)\n"
                "  VALUES ((SELECT id FROM probate.business_entity WHERE sos_id = {0}), {1}, {2}, {3})\n"
                "  ON CONFLICT (entity_id, person_name, title, source) DO NOTHING;".format(
                    sql_literal(str(entity.get("sos_id"))),
                    sql_literal(official.get("person_name")),
                    sql_literal(official.get("title") or ""),  # '' not NULL: NULL never conflicts
                    sql_literal(official.get("source") or "unknown"),
                )
            )
    lines.append("")

    lines.append("-- candidate links; a human decision always wins on conflict")
    lines.append("CREATE TEMP TABLE _incoming (left_id text, right_id text, status text) ON COMMIT DROP;")
    for link in result["matches"]:
        lines.append(_match_row(link, rules_version))
        lines.append(
            "INSERT INTO _incoming VALUES ({0}, {1}, {2});".format(
                sql_literal(link["left_id"]), sql_literal(link["right_id"]), sql_literal(link["status"])
            )
        )
    lines.append("")

    lines.append("-- where the matcher now disagrees with a preserved human decision")
    lines.append(
        "SELECT m.left_id, m.right_id, m.status AS human_status, m.reviewer, m.reviewed_at,\n"
        "       i.status AS matcher_status, m.score, m.flags\n"
        "FROM probate.entity_match m\n"
        "JOIN _incoming i ON i.left_id = m.left_id AND i.right_id = m.right_id\n"
        "WHERE m.reviewed_at IS NOT NULL AND m.status::text <> i.status\n"
        "ORDER BY m.left_id, m.right_id;"
    )
    lines.append("")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ cli ----


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="crossref.py --json output")
    parser.add_argument("--estates", help="the estate records the run used")
    parser.add_argument("--parcels", help="the parcel records the run used")
    parser.add_argument("--deeds", help="the deed records the run used")
    parser.add_argument("--entities", help="the business entity records the run used")
    parser.add_argument("--out", help="write SQL here instead of stdout")
    args = parser.parse_args(argv)

    with open(args.run, encoding="utf-8") as f:
        result = json.load(f)
    params = {
        key: getattr(args, key) for key in ("estates", "parcels", "deeds", "entities") if getattr(args, key)
    }
    sql = render(
        result,
        estates=load_records(args.estates) if args.estates else [],
        parcels=load_records(args.parcels) if args.parcels else [],
        deeds=load_records(args.deeds) if args.deeds else [],
        entities=load_records(args.entities) if args.entities else [],
        params={"inputs": params},
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(sql)
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    sys.exit(main())
