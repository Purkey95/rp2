"""SQLite-backed reference store. Postgres DDL lives in sql/postgres/ for deployment.

The store is deliberately thin: it applies migrations, hands out a connection, and
offers a few query helpers. All domain logic lives in the modules that use it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .clock import Clock

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "sql", "sqlite"))


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def loads(text: Optional[str], default: Any = None) -> Any:
    if text is None or text == "":
        return default
    return json.loads(text)


class Store:
    def __init__(self, path: str = ":memory:", clock: Optional[Clock] = None) -> None:
        self.path = path
        self.clock = clock or Clock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL" if path != ":memory:" else "PRAGMA journal_mode = MEMORY")

    # ---------------------------------------------------------- lifecycle ---

    def migrate(self, sql_dir: str = SQL_DIR) -> List[str]:
        applied = []
        for name in sorted(os.listdir(sql_dir)):
            if not name.endswith(".sql"):
                continue
            with open(os.path.join(sql_dir, name), encoding="utf-8") as f:
                self.conn.executescript(f.read())
            applied.append(name)
        self.conn.commit()
        return applied

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return self.clock.now_iso()

    # ------------------------------------------------------------ helpers ---

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self.conn.executemany(sql, rows)

    def commit(self) -> None:
        self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row is not None else None

    def insert(self, table: str, values: Dict[str, Any]) -> int:
        cols = list(values)
        sql = "INSERT INTO {0} ({1}) VALUES ({2})".format(table, ", ".join(cols), ", ".join("?" for _ in cols))
        cur = self.conn.execute(sql, [values[c] for c in cols])
        return int(cur.lastrowid)

    def update(self, table: str, values: Dict[str, Any], where: str, params: Sequence[Any]) -> int:
        cols = list(values)
        sql = "UPDATE {0} SET {1} WHERE {2}".format(table, ", ".join("{0} = ?".format(c) for c in cols), where)
        cur = self.conn.execute(sql, [values[c] for c in cols] + list(params))
        return cur.rowcount
