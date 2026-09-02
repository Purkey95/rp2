#!/usr/bin/env python3
"""Map a vendor CSV onto the JSONL crossref.py reads. Deterministic code parses; agents orchestrate.

    python3 intake/adapt.py --input export.csv --map intake/maps/estates.json \\
        --out estate_cases.jsonl --county MECKLENBURG \\
        --source-url-base https://example.invalid/estates [--since 2026-08-01]

Every county source has its own column names, date formats and quirks, and
this engine keeps all of that in one place: a map file. A map names the
output record type, which source column feeds which schema.sql column, how to
type it, the natural key, what is required, and (for the SOS) a child file to
nest. The map is the ONLY place a rename happens. When a vendor's layout turns
out to differ from the fixture it was written against, the map is corrected,
not the code.

Rules the engine enforces, per agents/skills/intake-records.md:

  * a field the source did not supply stays absent -- nothing is inferred
  * every record carries county, source_url and retrieved_at
  * a required column missing, a date that will not parse, a number that is
    not a number, a county that is not the one asked for, or a natural key
    seen twice is a REJECT with a reason, never a fix
  * the good records are written, the rejects are written beside them, and
    the exit status is non-zero if anything was rejected (--allow-rejects to
    override), so a chained run never runs over a partial pull

No HTTP anywhere. The estates source in particular has no automated access
route today (see agents/sources.md); this reads a file someone exported.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

from load_run import DEED_COLUMNS, ENTITY_COLUMNS, ESTATE_COLUMNS, PARCEL_COLUMNS  # noqa: E402

ALLOWED = {
    "estate_case": set(ESTATE_COLUMNS),
    "parcel": set(PARCEL_COLUMNS),
    "deed": set(DEED_COLUMNS),
    "business_entity": set(ENTITY_COLUMNS) | {"officials"},
}
# Statewide sources have no county; everything else must carry the one asked for.
COUNTY_LESS = {"business_entity"}

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d %Y")


class MapError(ValueError):
    """The map file is wrong. Fix the map, not the data."""


# ------------------------------------------------------------- typing ------


def parse_date(raw):
    """ISO date or None; raises ValueError when the source wrote something else."""
    text = (raw or "").strip()
    if not text:
        return None
    text = text.split("T")[0].split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("unparseable date {0!r}".format(raw))


def parse_number(raw):
    text = re.sub(r"[,$\s]", "", raw or "")
    if not text:
        return None
    try:
        return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
    except ValueError:
        raise ValueError("unparseable number {0!r}".format(raw))


def parse_bool(raw):
    text = (raw or "").strip().upper()
    if not text:
        return None
    if text in ("1", "Y", "YES", "TRUE", "T", "DOMESTIC"):
        return True
    if text in ("0", "N", "NO", "FALSE", "F", "FOREIGN"):
        return False
    raise ValueError("unparseable boolean {0!r}".format(raw))


TYPES = {"text": lambda s: (s or "").strip() or None, "date": parse_date, "number": parse_number, "bool": parse_bool}


# ------------------------------------------------------------- mapping -----


def load_map(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    record_type = spec.get("record_type")
    if record_type not in ALLOWED:
        raise MapError("record_type must be one of {0}".format(sorted(ALLOWED)))
    unknown = set(spec.get("fields", {})) - ALLOWED[record_type]
    if unknown:
        raise MapError("fields not in schema.sql for {0}: {1}".format(record_type, sorted(unknown)))
    for name, field in spec.get("fields", {}).items():
        if not any(k in field for k in ("from", "const", "template", "join", "arg")):
            raise MapError("field {0} needs one of from/const/template/join/arg".format(name))
        if field.get("type", "text") not in TYPES:
            raise MapError("field {0}: unknown type {1}".format(name, field.get("type")))
    return spec


def _value(field, row, args):
    """Raw string for one output field, before typing. None when the source has nothing."""
    if "const" in field:
        return field["const"]
    if "arg" in field:
        return args.get(field["arg"])
    if "from" in field:
        return row.get(field["from"])
    if "join" in field:
        parts = [(row.get(c) or "").strip() for c in field["join"]]
        parts = [p for p in parts if p]
        return field.get("sep", " ").join(parts) if parts else None
    if "template" in field:
        names = re.findall(r"{(\w+)}", field["template"])
        values = {n: (row.get(n) or args.get(n) or "").strip() for n in names}
        source_names = [n for n in names if n in row]
        if source_names and not any(values[n] for n in source_names):
            return None  # a template over empty source columns is empty, not "/ /"
        return re.sub(r"\s+", " ", field["template"].format(**values)).strip()
    return None


def build_record(row, spec, args):
    """One output record from one source row. Returns (record, reasons)."""
    record, reasons = {}, []
    for name, field in spec.get("fields", {}).items():
        raw = _value(field, row, args)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            value = TYPES[field.get("type", "text")](raw) if isinstance(raw, str) else raw
        except ValueError as exc:
            reasons.append("{0}: {1}".format(name, exc))
            continue
        if value is not None:
            record[name] = value
    return record, reasons


def _passes_filters(row, spec):
    for rule in spec.get("filters", []):
        if not re.search(rule["regex"], row.get(rule["column"]) or ""):
            return False
    return True


def adapt(rows, spec, args, child_rows=None):
    """Rows in, (records, rejects, filtered_count) out. Nothing is inferred; failures are listed."""
    record_type = spec["record_type"]
    required = set(spec.get("required", [])) | {"source_url", "retrieved_at"}
    if record_type not in COUNTY_LESS:
        required.add("county")
    natural_key = spec.get("natural_key", [])
    children = spec.get("children", {})
    by_parent = {}
    for child_name, child in children.items():
        groups = {}
        for crow in child_rows or []:
            groups.setdefault((crow.get(child["join_child"]) or "").strip(), []).append(crow)
        by_parent[child_name] = (child, groups)

    records, rejects, seen, filtered = [], [], {}, 0
    for line_no, row in enumerate(rows, start=2):  # header is line 1
        if not _passes_filters(row, spec):
            filtered += 1
            continue
        record, reasons = build_record(row, spec, args)
        for column in sorted(required):
            if record.get(column) in (None, ""):
                reasons.append("required column {0} is missing".format(column))
        if args.get("county") and record_type not in COUNTY_LESS and record.get("county") != args["county"]:
            reasons.append("county {0!r} is not {1!r}".format(record.get("county"), args["county"]))
        if args.get("since") and spec.get("since_field"):
            value = record.get(spec["since_field"])
            if value and value < args["since"]:
                filtered += 1
                continue
        if natural_key and not reasons:
            key = tuple(record.get(c) for c in natural_key)
            if key in seen:
                reasons.append("natural key {0} already seen at line {1}".format(key, seen[key]))
            else:
                seen[key] = line_no
        for child_name, (child, groups) in by_parent.items():
            parent_key = (row.get(child["join_parent"]) or "").strip()
            nested = []
            for crow in groups.get(parent_key, []):
                crecord, creasons = build_record(crow, child, args)
                if creasons:
                    reasons.extend("{0}: {1}".format(child_name, r) for r in creasons)
                elif crecord:
                    nested.append(crecord)
            record[child_name] = nested
        if reasons:
            rejects.append({"line": line_no, "row": row, "reasons": reasons})
        else:
            records.append(record)
    return records, rejects, filtered


# ------------------------------------------------------------- io ----------


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="vendor CSV")
    parser.add_argument("--map", required=True, help="map file from intake/maps/")
    parser.add_argument("--out", required=True, help="JSONL to write")
    parser.add_argument("--county", help="county spelling used throughout probate.*; required unless statewide")
    parser.add_argument("--source-url-base", default="", help="prefix for source_url templates")
    parser.add_argument("--retrieved-at", help="ISO timestamp; default now (UTC)")
    parser.add_argument("--since", help="keep only records whose since_field is on/after this ISO date")
    parser.add_argument("--child", action="append", default=[], help="name=path of a child CSV (e.g. officials=x.csv)")
    parser.add_argument("--rejects", help="JSONL of rejected rows with reasons (default: <out>.rejects.jsonl)")
    parser.add_argument("--allow-rejects", action="store_true", help="exit 0 even if rows were rejected")
    args = parser.parse_args(argv)

    spec = load_map(args.map)
    if spec["record_type"] not in COUNTY_LESS and not args.county:
        parser.error("--county is required for {0}".format(spec["record_type"]))
    retrieved_at = args.retrieved_at or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    arg_values = {
        "county": args.county,
        "base": args.source_url_base.rstrip("/"),
        "retrieved_at": retrieved_at,
        "since": args.since,
    }
    child_rows = []
    for item in args.child:
        name, _, path = item.partition("=")
        if name not in spec.get("children", {}):
            parser.error("map has no child named {0!r}".format(name))
        child_rows.extend(read_csv(path))
    if spec.get("children") and not args.child:
        parser.error("map declares children {0}; pass --child name=path".format(sorted(spec["children"])))

    records, rejects, filtered = adapt(read_csv(args.input), spec, arg_values, child_rows)
    write_jsonl(args.out, records)
    rejects_path = args.rejects or (args.out + ".rejects.jsonl")
    write_jsonl(rejects_path, rejects)
    sys.stderr.write(
        "{0}: {1} written, {2} rejected, {3} filtered out -> {4}{5}\n".format(
            spec["record_type"], len(records), len(rejects), filtered, args.out,
            " (rejects in {0})".format(rejects_path) if rejects else "",
        )
    )
    if rejects and not args.allow_rejects:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
