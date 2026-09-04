#!/usr/bin/env python3
"""Persist cross-reference runs, so there is a yesterday to compare today against.

crossref.py is a stateless batch job; on its own it cannot say what is *new*, and
it cannot notice that a parcel it confirmed last month has since been conveyed.
This module gives it memory: a SQLite file (stdlib, no server) that mirrors the
Postgres model in schema.sql closely enough that loading one into the other is
mechanical.

What is kept, per run:

  match_run          when, which tool/rules versions, how many input rows
  match_observation  every candidate the run produced, verbatim -- the history
  entity_match       one row per estate/parcel pair with first-seen / last-seen
                     run dates: first_seen_as_of is the daily cohort
  parcel_snapshot    the assessor row for every parcel we are tracking, so the
                     next run can see the owner string change
  deed               every recorded deed we have been shown
  parcel_transfer    each detected departure of a parcel from an estate, with
                     the evidence, and the outcome it implies for the match

Transfer detection runs after every load, over every tracked (non-rejected)
pair, and is sticky: once a parcel is `transferred` the matcher re-confirming it
next week (assessor lag) does not bring it back. The evidence lives in
parcel_transfer; the lead list excludes it. Pure stdlib.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import transfers  # noqa: E402

STORE_VERSION = "1"
TRACKED = ("confirmed", "pending")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_run (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of               TEXT NOT NULL,      -- YYYY-MM-DD the inputs describe
    loaded_at           TEXT NOT NULL,
    tool_version        TEXT NOT NULL,
    rules_version       TEXT,
    estate_cases        INTEGER NOT NULL,
    parcels             INTEGER NOT NULL,
    deeds               INTEGER NOT NULL,
    params              TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS estate_case (
    left_id             TEXT PRIMARY KEY,   -- COUNTY/file number, as in entity_match
    county              TEXT,
    file_number         TEXT,
    decedent_name       TEXT,
    personal_rep_name   TEXT,
    pr_mailing_address  TEXT,
    date_of_death       TEXT,
    filing_date         TEXT,
    case_status         TEXT,
    first_seen_run      INTEGER NOT NULL,
    last_seen_run       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS parcel_snapshot (
    run_id              INTEGER NOT NULL,
    right_id            TEXT NOT NULL,      -- COUNTY/PIN, as in entity_match
    owner_name          TEXT,
    owner_mailing_address TEXT,
    situs_address       TEXT,
    land_use            TEXT,
    assessed_value      REAL,
    deed_book           TEXT,
    deed_page           TEXT,
    last_sale_date      TEXT,
    PRIMARY KEY (run_id, right_id)
);

CREATE TABLE IF NOT EXISTS deed (
    deed_key            TEXT PRIMARY KEY,   -- transfers.deed_key()
    county              TEXT,
    instrument_number   TEXT,
    book                TEXT,
    page                TEXT,
    recorded_date       TEXT,
    instrument_type     TEXT,
    grantor_name        TEXT,
    grantee_name        TEXT,
    parcel_pin          TEXT,
    parcel_key          TEXT,               -- normalized COUNTY/PIN for joins
    first_seen_run      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS deed_parcel_key ON deed (parcel_key);

CREATE TABLE IF NOT EXISTS match_observation (
    run_id              INTEGER NOT NULL,
    left_id             TEXT NOT NULL,
    right_id            TEXT NOT NULL,
    match_tier          TEXT NOT NULL,
    score               REAL NOT NULL,
    evidence            TEXT NOT NULL,      -- JSON list
    flags               TEXT NOT NULL,      -- JSON list
    status              TEXT NOT NULL,      -- what the rules said on this run
    PRIMARY KEY (run_id, left_id, right_id)
);

CREATE TABLE IF NOT EXISTS entity_match (
    left_id             TEXT NOT NULL,
    right_id            TEXT NOT NULL,
    first_seen_run      INTEGER NOT NULL,
    first_seen_as_of    TEXT NOT NULL,
    last_seen_run       INTEGER NOT NULL,
    last_seen_as_of     TEXT NOT NULL,
    match_tier          TEXT NOT NULL,
    score               REAL NOT NULL,
    evidence            TEXT NOT NULL,
    flags               TEXT NOT NULL,
    model_status        TEXT NOT NULL,      -- latest rule disposition
    status              TEXT NOT NULL,      -- current: confirmed/pending/rejected/transferred
    transferred_run     INTEGER,
    transferred_as_of   TEXT,
    reviewer            TEXT,
    reviewed_at         TEXT,
    review_note         TEXT,
    PRIMARY KEY (left_id, right_id)
);
CREATE INDEX IF NOT EXISTS entity_match_status ON entity_match (status, score DESC);
CREATE INDEX IF NOT EXISTS entity_match_cohort ON entity_match (first_seen_as_of);

CREATE TABLE IF NOT EXISTS parcel_transfer (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL,
    as_of               TEXT NOT NULL,
    left_id             TEXT NOT NULL,
    right_id            TEXT NOT NULL,
    kind                TEXT NOT NULL,      -- deed_from_estate | owner_changed | owner_restyled |
                                            -- sale_date_advanced | namesake_conveyance | ambiguous_conveyance
    basis               TEXT NOT NULL,      -- deed key, or 'snapshot:<prev_run>-><run>'
    detail              TEXT NOT NULL,      -- JSON
    status_before       TEXT NOT NULL,
    status_after        TEXT NOT NULL,
    UNIQUE (left_id, right_id, kind, basis)
);
"""

# What each transfer event does to the match. None = evidence only, no change.
OUTCOME = {
    "deed_from_estate": "transferred",
    "owner_changed": "transferred",
    "namesake_conveyance": "pending",
    "owner_restyled": None,
    "sale_date_advanced": None,
    "ambiguous_conveyance": None,
}


# ------------------------------------------------------------------ open ---


def connect(path):
    """Open (creating if needed) a store. ':memory:' works for tests."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('store_version', ?)", (STORE_VERSION,))
    conn.commit()
    return conn


def _j(value):
    return json.dumps(value, sort_keys=True)


def _norm_right(right_id):
    """'MECKLENBURG/045-121-08' -> the normalized key deeds are joined on."""
    county, _, pin = (right_id or "").partition("/")
    return crossref.pin_key(county, pin)


def _owner_key(owner_name, rules):
    """Owner string reduced to its parties and markers, so punctuation churn is not a 'change'."""
    fmt = rules.get("name_formats", {}).get("parcel", "last_first")
    parties = crossref.split_parties(owner_name or "", fmt, rules)
    return " & ".join(sorted(" ".join([p["normalized"]] + p["markers"]) for p in parties if p["normalized"]))


def _decedent_on_owner(owner_name, estate, rules):
    fmt = rules.get("name_formats", {}).get("parcel", "last_first")
    decedent, _ = transfers.estate_people(estate, rules)
    return any(not p["is_organization"] and p["key_fl"] == decedent["key_fl"] for p in crossref.split_parties(owner_name or "", fmt, rules))


# ------------------------------------------------------------------ load ---


def load_run(conn, result, estates, parcels, deeds, rules, as_of=None, params=None):
    """Record one crossref run and everything needed to compare the next one to it.

    `result` is crossref.crossref()'s return value for exactly these inputs.
    Returns the run id and a delta: what is new, what changed, what left.
    """
    as_of = as_of or dt.date.today().isoformat()
    before = {(r["left_id"], r["right_id"]): r["status"] for r in conn.execute("SELECT left_id, right_id, status FROM entity_match")}

    cur = conn.execute(
        "INSERT INTO match_run (as_of, loaded_at, tool_version, rules_version, estate_cases, parcels, deeds, params) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            as_of,
            dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            result["run"]["tool_version"],
            result["run"].get("rules_version"),
            result["run"]["estate_cases"],
            result["run"]["parcels"],
            result["run"]["deeds"],
            _j(params or {}),
        ),
    )
    run_id = cur.lastrowid

    _upsert_estates(conn, run_id, estates)
    _insert_deeds(conn, run_id, deeds)
    _record_observations(conn, run_id, as_of, result["matches"])
    tracked = _tracked_right_ids(conn)
    _snapshot_parcels(conn, run_id, parcels, tracked)
    events = detect_transfers(conn, run_id, as_of, rules)
    conn.commit()

    after = {(r["left_id"], r["right_id"]): r["status"] for r in conn.execute("SELECT left_id, right_id, status FROM entity_match")}
    return run_id, _delta(conn, run_id, before, after, events)


def _upsert_estates(conn, run_id, estates):
    for estate in estates:
        left_id = "{0}/{1}".format(estate.get("county"), estate.get("file_number"))
        conn.execute(
            """INSERT INTO estate_case (left_id, county, file_number, decedent_name, personal_rep_name,
                   pr_mailing_address, date_of_death, filing_date, case_status, first_seen_run, last_seen_run)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (left_id) DO UPDATE SET
                   decedent_name = excluded.decedent_name,
                   personal_rep_name = excluded.personal_rep_name,
                   pr_mailing_address = excluded.pr_mailing_address,
                   date_of_death = excluded.date_of_death,
                   filing_date = excluded.filing_date,
                   case_status = excluded.case_status,
                   last_seen_run = excluded.last_seen_run""",
            (
                left_id,
                estate.get("county"),
                estate.get("file_number"),
                estate.get("decedent_name"),
                estate.get("personal_rep_name"),
                estate.get("pr_mailing_address"),
                (estate.get("date_of_death") or None),
                (estate.get("filing_date") or None),
                estate.get("case_status"),
                run_id,
                run_id,
            ),
        )


def _insert_deeds(conn, run_id, deeds):
    for deed in deeds or []:
        conn.execute(
            """INSERT OR IGNORE INTO deed (deed_key, county, instrument_number, book, page, recorded_date,
                   instrument_type, grantor_name, grantee_name, parcel_pin, parcel_key, first_seen_run)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transfers.deed_key(deed),
                deed.get("county"),
                deed.get("instrument_number"),
                deed.get("book"),
                deed.get("page"),
                (deed.get("recorded_date") or None),
                deed.get("instrument_type"),
                deed.get("grantor_name"),
                deed.get("grantee_name"),
                deed.get("parcel_pin"),
                transfers.deed_parcel_key(deed),
                run_id,
            ),
        )


def _record_observations(conn, run_id, as_of, links):
    for link in links:
        conn.execute(
            "INSERT INTO match_observation (run_id, left_id, right_id, match_tier, score, evidence, flags, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, link["left_id"], link["right_id"], link["match_tier"], link["score"], _j(link["evidence"]), _j(link["flags"]), link["status"]),
        )
        row = conn.execute("SELECT status FROM entity_match WHERE left_id = ? AND right_id = ?", (link["left_id"], link["right_id"])).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO entity_match (left_id, right_id, first_seen_run, first_seen_as_of, last_seen_run, last_seen_as_of,
                       match_tier, score, evidence, flags, model_status, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    link["left_id"],
                    link["right_id"],
                    run_id,
                    as_of,
                    run_id,
                    as_of,
                    link["match_tier"],
                    link["score"],
                    _j(link["evidence"]),
                    _j(link["flags"]),
                    link["status"],
                    link["status"],
                ),
            )
            continue
        # A transfer or a reviewer's decision outranks the rules; otherwise the
        # latest disposition is the current one.
        keep = row["status"] == "transferred" or _reviewed(conn, link)
        conn.execute(
            """UPDATE entity_match SET last_seen_run = ?, last_seen_as_of = ?, match_tier = ?, score = ?, evidence = ?, flags = ?,
                   model_status = ?, status = CASE WHEN ? THEN status ELSE ? END
               WHERE left_id = ? AND right_id = ?""",
            (
                run_id,
                as_of,
                link["match_tier"],
                link["score"],
                _j(link["evidence"]),
                _j(link["flags"]),
                link["status"],
                keep,
                link["status"],
                link["left_id"],
                link["right_id"],
            ),
        )


def _reviewed(conn, link):
    row = conn.execute("SELECT reviewer FROM entity_match WHERE left_id = ? AND right_id = ?", (link["left_id"], link["right_id"])).fetchone()
    return bool(row and row["reviewer"])


def _tracked_right_ids(conn):
    return {r["right_id"] for r in conn.execute("SELECT DISTINCT right_id FROM entity_match WHERE status IN (?, ?)", TRACKED)}


def _snapshot_parcels(conn, run_id, parcels, tracked):
    """Keep the assessor row for tracked parcels only: the full county index is too big to version."""
    for parcel in parcels:
        right_id = "{0}/{1}".format(parcel.get("county"), parcel.get("pin"))
        if right_id not in tracked:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO parcel_snapshot (run_id, right_id, owner_name, owner_mailing_address, situs_address,
                   land_use, assessed_value, deed_book, deed_page, last_sale_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                right_id,
                parcel.get("owner_name"),
                parcel.get("owner_mailing_address"),
                parcel.get("situs_address"),
                parcel.get("land_use"),
                parcel.get("assessed_value"),
                parcel.get("deed_book"),
                parcel.get("deed_page"),
                (parcel.get("last_sale_date") or None),
            ),
        )


# -------------------------------------------------------------- transfers --


def detect_transfers(conn, run_id, as_of, rules):
    """Look for every tracked parcel having left its estate, and apply what that implies."""
    events = []
    pairs = conn.execute(
        "SELECT m.left_id, m.right_id, m.status FROM entity_match m WHERE m.status IN (?, ?) ORDER BY m.left_id, m.right_id", TRACKED
    ).fetchall()
    for pair in pairs:
        estate = conn.execute("SELECT * FROM estate_case WHERE left_id = ?", (pair["left_id"],)).fetchone()
        if estate is None:
            continue
        estate = dict(estate)
        status = pair["status"]
        for event in _deed_events(conn, pair, estate, rules) + _snapshot_events(conn, run_id, pair, estate, rules):
            status_after = OUTCOME.get(event["kind"]) or status
            cur = conn.execute(
                """INSERT OR IGNORE INTO parcel_transfer (run_id, as_of, left_id, right_id, kind, basis, detail, status_before, status_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, as_of, pair["left_id"], pair["right_id"], event["kind"], event["basis"], _j(event["detail"]), status, status_after),
            )
            if cur.rowcount == 0:
                continue  # already on file from an earlier run
            if status_after != status:
                conn.execute(
                    """UPDATE entity_match SET status = ?,
                           transferred_run = CASE WHEN ? = 'transferred' THEN ? ELSE transferred_run END,
                           transferred_as_of = CASE WHEN ? = 'transferred' THEN ? ELSE transferred_as_of END
                       WHERE left_id = ? AND right_id = ?""",
                    (status_after, status_after, run_id, status_after, as_of, pair["left_id"], pair["right_id"]),
                )
                status = status_after
            events.append(dict(event, left_id=pair["left_id"], right_id=pair["right_id"], status_after=status_after, run_id=run_id, as_of=as_of))
    return events


def _deed_events(conn, pair, estate, rules):
    events = []
    for deed in conn.execute("SELECT * FROM deed WHERE parcel_key = ? ORDER BY recorded_date, deed_key", (_norm_right(pair["right_id"]),)):
        reading = transfers.classify_conveyance(dict(deed), estate, rules)
        if reading is None:
            continue
        kind = {"estate": "deed_from_estate", "namesake": "namesake_conveyance", "ambiguous": "ambiguous_conveyance"}[reading["relation"]]
        events.append({"kind": kind, "basis": deed["deed_key"], "detail": reading})
    return events


def _snapshot_events(conn, run_id, pair, estate, rules):
    now = conn.execute("SELECT * FROM parcel_snapshot WHERE run_id = ? AND right_id = ?", (run_id, pair["right_id"])).fetchone()
    prev = conn.execute("SELECT * FROM parcel_snapshot WHERE run_id < ? AND right_id = ? ORDER BY run_id DESC LIMIT 1", (run_id, pair["right_id"])).fetchone()
    if now is None or prev is None:
        return []
    basis = "snapshot:{0}->{1}".format(prev["run_id"], run_id)
    events = []
    if _owner_key(now["owner_name"], rules) != _owner_key(prev["owner_name"], rules):
        kind = "owner_restyled" if _decedent_on_owner(now["owner_name"], estate, rules) else "owner_changed"
        events.append({"kind": kind, "basis": basis, "detail": {"owner_before": prev["owner_name"], "owner_after": now["owner_name"]}})
    death = transfers.death_or_filing(estate)
    if now["last_sale_date"] and (prev["last_sale_date"] or "") < now["last_sale_date"] and death and now["last_sale_date"] > death:
        events.append(
            {"kind": "sale_date_advanced", "basis": basis, "detail": {"last_sale_before": prev["last_sale_date"], "last_sale_after": now["last_sale_date"]}}
        )
    return events


# ----------------------------------------------------------------- delta ---


def _delta(conn, run_id, before, after, events):
    new = defaultdict(list)
    changed = []
    for key, status in sorted(after.items()):
        if key not in before:
            new[status].append(key)
        elif before[key] != status:
            changed.append({"left_id": key[0], "right_id": key[1], "before": before[key], "after": status})
    unobserved = [
        (r["left_id"], r["right_id"], r["status"])
        for r in conn.execute(
            "SELECT left_id, right_id, status FROM entity_match WHERE status IN (?, ?) AND last_seen_run < ? ORDER BY left_id, right_id", TRACKED + (run_id,)
        )
    ]
    return {
        "run_id": run_id,
        "new_confirmed": new["confirmed"],
        "new_pending": new["pending"],
        "new_rejected": len(new["rejected"]),
        "changed": changed,
        "transfers": events,
        "unobserved": unobserved,
    }


def format_delta(delta):
    lines = [
        "run {0}: {1} new confirmed, {2} new pending, {3} new rejected".format(
            delta["run_id"], len(delta["new_confirmed"]), len(delta["new_pending"]), delta["new_rejected"]
        )
    ]
    for label, key in (("NEW CONFIRMED", "new_confirmed"), ("NEW PENDING", "new_pending")):
        if delta[key]:
            lines.append(label)
            lines.extend("  {0} -> {1}".format(left, right) for left, right in delta[key])
    if delta["changed"]:
        lines.append("STATUS CHANGES")
        lines.extend("  {0} -> {1}: {2} -> {3}".format(c["left_id"], c["right_id"], c["before"], c["after"]) for c in delta["changed"])
    if delta["transfers"]:
        lines.append("TRANSFERS DETECTED")
        for e in delta["transfers"]:
            lines.append("  [{0}] {1} -> {2}  now {3}".format(e["kind"], e["left_id"], e["right_id"], e["status_after"]))
            detail = e["detail"]
            if "grantee_name" in detail:
                lines.append(
                    "      {0} {1}: {2} -> {3} ({4}; {5})".format(
                        detail.get("recorded_date"),
                        detail.get("instrument_type") or "deed",
                        detail.get("grantor_name"),
                        detail.get("grantee_name"),
                        detail["basis"],
                        detail["grantee_relation"],
                    )
                )
            elif "owner_after" in detail:
                lines.append("      owner: {0} -> {1}".format(detail["owner_before"], detail["owner_after"]))
            elif "last_sale_after" in detail:
                lines.append("      last sale: {0} -> {1}".format(detail["last_sale_before"], detail["last_sale_after"]))
    if delta["unobserved"]:
        lines.append("TRACKED BUT NOT A CANDIDATE THIS RUN (owner string no longer carries the decedent?)")
        lines.extend("  [{2}] {0} -> {1}".format(*row) for row in delta["unobserved"])
    return "\n".join(lines)
