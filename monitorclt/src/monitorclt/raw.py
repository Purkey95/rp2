"""Raw capture: what was fetched, byte for byte, hashed and kept.

Everything derived is recomputable from here. A parser bug is a replay; a county
changing its markup is diagnosable from the exact bytes that broke it.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .store import Store


def content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def capture(store: Store, source: str, county: str, body: bytes, url: Optional[str] = None, content_type: Optional[str] = None) -> int:
    """Store a fetched body once per (source, county, hash); return the capture id."""
    digest = content_hash(body)
    existing = store.one("SELECT id FROM raw_capture WHERE source = ? AND county = ? AND content_hash = ?", (source, county, digest))
    if existing:
        return int(existing["id"])
    return store.insert(
        "raw_capture",
        {
            "source": source,
            "county": county,
            "url": url,
            "fetched_at": store.now(),
            "content_type": content_type,
            "content_hash": digest,
            "byte_size": len(body),
            "body": body,
        },
    )


def read(store: Store, capture_id: int) -> bytes:
    row = store.one("SELECT body FROM raw_capture WHERE id = ?", (capture_id,))
    if row is None:
        raise KeyError("no raw capture {0}".format(capture_id))
    return bytes(row["body"])
