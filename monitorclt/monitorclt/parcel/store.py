"""SQLite index of parcels, with provenance and observation timestamps.

Every row records ``observed_at`` and the ``run_id`` that produced it, so the
index can answer "what did we know, and when?" — the precondition for the
backtest described in
``second-brain/wiki/property-signal-scoring-and-calibration.md``. Without it,
replaying a signal against historical data is impossible.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .model import Parcel

SCHEMA = """
CREATE TABLE IF NOT EXISTS load_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    where_clause TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    record_count INTEGER
);

CREATE TABLE IF NOT EXISTS parcels (
    parcel_key          TEXT PRIMARY KEY,
    pid                 TEXT,
    property_id         INTEGER,
    owner_raw           TEXT,
    owner_type          TEXT,
    owner_markers       TEXT,
    indicates_decedent  INTEGER NOT NULL DEFAULT 0,
    care_of             TEXT,
    situs_raw           TEXT,
    situs_key           TEXT,
    situs_key_with_unit TEXT,
    situs_city          TEXT,
    mail_key            TEXT,
    mail_city           TEXT,
    mail_state          TEXT,
    is_absentee         INTEGER NOT NULL DEFAULT 0,
    is_out_of_state     INTEGER NOT NULL DEFAULT 0,
    is_international    INTEGER NOT NULL DEFAULT 0,
    is_out_of_area      INTEGER NOT NULL DEFAULT 0,
    land_value          REAL,
    building_value      REAL,
    total_value         REAL,
    last_sale_date      TEXT,
    last_sale_price     REAL,
    deed_book           TEXT,
    deed_page           TEXT,
    property_use        TEXT,
    acres               REAL,
    municipality        TEXT,
    observed_at         TEXT NOT NULL,
    run_id              INTEGER,
    source              TEXT
);

CREATE TABLE IF NOT EXISTS owner_names (
    parcel_key        TEXT NOT NULL,
    surname           TEXT NOT NULL,
    given             TEXT,
    suffix            TEXT,
    blocking_key      TEXT NOT NULL,
    loose_blocking_key TEXT NOT NULL,
    PRIMARY KEY (parcel_key, surname, given)
);

CREATE INDEX IF NOT EXISTS ix_parcels_situs      ON parcels (situs_key);
CREATE INDEX IF NOT EXISTS ix_parcels_owner_type ON parcels (owner_type);
CREATE INDEX IF NOT EXISTS ix_parcels_decedent   ON parcels (indicates_decedent);
CREATE INDEX IF NOT EXISTS ix_parcels_mail_state ON parcels (mail_state);
CREATE INDEX IF NOT EXISTS ix_owner_block        ON owner_names (blocking_key);
CREATE INDEX IF NOT EXISTS ix_owner_loose_block  ON owner_names (loose_blocking_key);
"""

_PARCEL_COLUMNS = (
    "parcel_key", "pid", "property_id", "owner_raw", "owner_type", "owner_markers",
    "indicates_decedent", "care_of", "situs_raw", "situs_key", "situs_key_with_unit",
    "situs_city", "mail_key", "mail_city", "mail_state", "is_absentee",
    "is_out_of_state", "is_international", "is_out_of_area", "land_value", "building_value",
    "total_value", "last_sale_date", "last_sale_price", "deed_book", "deed_page",
    "property_use", "acres", "municipality", "observed_at", "run_id", "source",
)


def _row_values(parcel: Parcel, run_id: Optional[int]) -> List[Any]:
    return [
        parcel.parcel_key,
        parcel.pid,
        parcel.property_id,
        parcel.owner_raw,
        parcel.owner.owner_type,
        ",".join(parcel.owner.markers) or None,
        1 if parcel.indicates_decedent else 0,
        parcel.owner.care_of,
        parcel.situs_raw,
        parcel.situs.key or None,
        parcel.situs.key_with_unit or None,
        parcel.situs.city,
        parcel.mailing.key or None,
        parcel.mailing.city,
        parcel.mailing.state,
        1 if parcel.is_absentee else 0,
        1 if parcel.is_out_of_state else 0,
        1 if parcel.is_international else 0,
        1 if parcel.is_out_of_area else 0,
        parcel.land_value,
        parcel.building_value,
        parcel.total_value,
        parcel.last_sale_date.isoformat() if parcel.last_sale_date else None,
        parcel.last_sale_price,
        parcel.deed_book,
        parcel.deed_page,
        parcel.property_use,
        parcel.acres,
        parcel.municipality,
        parcel.observed_at.isoformat(),
        run_id,
        parcel.source,
    ]


class ParcelStore:
    """Owns the SQLite connection and the write path."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ParcelStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start_run(self, source: str, where_clause: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO load_runs (source, where_clause, started_at) VALUES (?, ?, ?)",
            (source, where_clause, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, record_count: int) -> None:
        self.connection.execute(
            "UPDATE load_runs SET finished_at = ?, record_count = ? WHERE run_id = ?",
            (datetime.now(timezone.utc).isoformat(), record_count, run_id),
        )
        self.connection.commit()

    def upsert(self, parcels: Iterable[Parcel], run_id: Optional[int] = None) -> int:
        """Insert or replace parcels and their owner-name blocking rows."""
        placeholders = ", ".join("?" for _ in _PARCEL_COLUMNS)
        statement = (
            "INSERT OR REPLACE INTO parcels (" + ", ".join(_PARCEL_COLUMNS) + ") VALUES (" + placeholders + ")"
        )
        written = 0
        for parcel in parcels:
            if not parcel.parcel_key:
                continue
            self.connection.execute(statement, _row_values(parcel, run_id))
            self.connection.execute("DELETE FROM owner_names WHERE parcel_key = ?", (parcel.parcel_key,))
            for person in parcel.owner.persons:
                if not person.surname:
                    continue
                self.connection.execute(
                    "INSERT OR REPLACE INTO owner_names "
                    "(parcel_key, surname, given, suffix, blocking_key, loose_blocking_key) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        parcel.parcel_key,
                        person.surname,
                        person.given,
                        person.suffix,
                        person.blocking_key,
                        person.loose_blocking_key,
                    ),
                )
            written += 1
            if written % 5000 == 0:
                self.connection.commit()
        self.connection.commit()
        return written

    # --- Reads --------------------------------------------------------------

    def query(self, sql: str, parameters: Iterable[Any] = ()) -> List[sqlite3.Row]:
        return list(self.connection.execute(sql, tuple(parameters)))

    def iter_rows(self, sql: str, parameters: Iterable[Any] = ()) -> Iterator[sqlite3.Row]:
        for row in self.connection.execute(sql, tuple(parameters)):
            yield row

    def stats(self) -> Dict[str, Any]:
        """Counts used to sanity-check a load and to size signal populations."""
        totals = self.connection.execute(
            "SELECT COUNT(*) AS parcels, "
            "COUNT(DISTINCT pid) AS distinct_pids, "
            "SUM(indicates_decedent) AS decedent_marked, "
            "SUM(is_absentee) AS absentee, "
            "SUM(is_out_of_state) AS out_of_state, "
            "SUM(is_international) AS international, "
            "SUM(is_out_of_area) AS out_of_area "
            "FROM parcels"
        ).fetchone()
        by_type = self.connection.execute(
            "SELECT owner_type, COUNT(*) AS n FROM parcels GROUP BY owner_type ORDER BY n DESC"
        ).fetchall()
        result: Dict[str, Any] = {key: totals[key] for key in totals.keys()}
        result["by_owner_type"] = {row["owner_type"]: row["n"] for row in by_type}
        return result
