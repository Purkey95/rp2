"""Load the run into PostgreSQL. Step 2 of the flow.

Wraps load_run.py, so the transaction semantics, the upsert keys and the
don't-overturn-a-human rule are the same whether the load came from a terminal
or from a schedule. The Windmill part is only the connection: `database` is a
`postgresql` resource, which is how the pipeline's credentials stay out of the
script and inside something with an access list.

Connect as a role that holds probate_loader and nothing more. It does not need
to decide a match and 0003_rls.sql makes sure it cannot.
"""

from f.monitorclt.crossref import load_rules  # ships crossref.py to the worker
from f.monitorclt.load_run import execute, plan, summarize

from typing import TypedDict

import psycopg


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    dbname: str
    sslmode: str
    password: str


def main(
    database: postgresql,
    result: dict,
    estates: list = None,
    parcels: list = None,
    deeds: list = None,
):
    """Write one cross-reference run: sources, the run row, and every candidate.

    Pass the same source records that went into the matcher. Without them the
    match rows land but the views have nothing to join to, and the review queue
    comes back empty for a reason that takes an hour to find.
    """
    sources = {
        "estates": estates or [],
        "parcels": parcels or [],
        "deeds": deeds or [],
    }
    steps = plan(result, sources, load_rules(None))

    connection = psycopg.connect(
        host=database["host"],
        port=database["port"],
        user=database["user"],
        password=database["password"],
        dbname=database["dbname"],
        sslmode=database.get("sslmode", "prefer"),
    )
    try:
        written = execute(connection, steps)
    finally:
        connection.close()

    summary = summarize(result)
    summary["written"] = written
    return summary
