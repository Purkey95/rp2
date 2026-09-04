"""Stable public identifiers, so downstream systems can dedupe across runs."""

from __future__ import annotations

import hashlib


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(p.upper().strip() for p in parts).encode("utf-8")).hexdigest()[:16]  # nosec B324 - identifier, not security


def permalink(base_url: str, kind: str, ident: str) -> str:
    return "{0}/{1}/{2}".format(base_url.rstrip("/"), kind, ident)
