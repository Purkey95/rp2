"""Shared test scaffolding: a store with the Mecklenburg fixtures ingested."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
FIXTURES = os.path.normpath(os.path.join(HERE, "..", "fixtures", "mecklenburg"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from monitorclt.clock import Clock  # noqa: E402
from monitorclt.sources.transport import FixtureTransport  # noqa: E402
from monitorclt.store import Store  # noqa: E402

from monitorclt import counties, ingest  # noqa: E402

COUNTY = counties.mecklenburg.COUNTY


def fresh_store(when="2026-09-01T00:00:00Z"):
    store = Store(clock=Clock.fixed(when))
    store.migrate()
    return store


def ingested(when="2026-09-01T00:00:00Z", sources=None):
    store = fresh_store(when)
    results = ingest.ingest_county(store, COUNTY, FixtureTransport(FIXTURES), sources)
    assert all(r.ok for r in results), [r["error"] for r in results if not r.ok]
    return store


def resolved(when="2026-09-01T00:00:00Z"):
    from monitorclt.resolve import resolve

    store = ingested(when)
    resolve(store)
    return store


def fixture_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()
