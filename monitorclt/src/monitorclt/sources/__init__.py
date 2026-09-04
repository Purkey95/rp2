"""Connector framework: how a public-record source gets into the system.

A connector declares a SourceSpec (what the records look like, which fields name
people and parcels, which date is valid time, whether the pull is a full snapshot or
incremental) and implements fetch() and parse(). Transport (HTTP, recorded fixture)
is injected, so every connector is testable offline against the bytes it will see.
"""

from .base import Connector, Fetched, SourceSpec, registry  # noqa: F401
from .transport import FixtureTransport, HttpTransport, Transport  # noqa: F401
