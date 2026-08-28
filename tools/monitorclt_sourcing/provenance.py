"""Provenance layer for MonitorCLT signals — the anti-fabrication gate.

Adapted from HomeSignal's connector governance (their `sources/socrata.ts` +
`arcgis.ts`), which enforces one rule above all: **a signal that cannot trace
back to an official source record is never written.** MonitorCLT's dead-lettered
scrapers have the opposite failure mode — they emit rows with no way to tell a
real delinquency from a parse artifact. This layer fixes that.

A Signal is MonitorCLT's normalized unit of distress/opportunity evidence
(tax delinquency, tax sale, foreclosure, probate, code violation, lien, ...).
Every Signal carries where it came from and how sure we are.

The five governance rules (verbatim intent, ported from HomeSignal CLAUDE.md §8):
  1. ANTI-FABRICATION — every emitted signal carries a source_url. No URL that
     resolves to a record or at least a dataset landing page -> QUARANTINE, never emit.
  2. NEVER GUESS CLASSIFICATION — signal_type comes from an explicit type_map;
     an unmapped value is "unclassified", never inferred from free text.
  3. NEVER GUESS GEOGRAPHY — a precise lat/lng only if the row carries one (or a
     full geocodable address); otherwise geo_precision="jurisdiction".
  4. NEVER GUESS THE BUCKET — status -> lifecycle bucket is an exact lookup; an
     unmapped status is excluded AND surfaced in the run report for a human to map.
  5. QUARANTINE, DON'T STOP — a per-row failure is logged and skipped; the run
     continues and reports what it dropped and why.

Pure stdlib.
"""

from __future__ import annotations  # PEP 604 `X | None` annotations stay lazy on py3.8/3.9

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# Lifecycle buckets are defined per-source in the registry (status_to_bucket), but
# these are the canonical destinations a status may map to.
BUCKETS = {"active", "resolved", "informational"}

# record_url precision, high -> low. "record" points at one official record;
# "dataset" only at the source's landing page (weaker, but still non-fabricated).
CONFIDENCE_BY_PRECISION = {"record": "verified", "dataset": "sourced"}


@dataclass
class Signal:
    """One normalized, sourced piece of evidence about a property."""
    signal_id: str                 # stable hash of (source, native id, subject)
    source_name: str               # e.g. "Mecklenburg County Tax Collector"
    signal_type: str               # from type_map, else "unclassified"
    status: str                    # raw status as published
    bucket: str                    # active | resolved | informational
    source_url: str                # REQUIRED — record or dataset landing page
    url_precision: str             # "record" | "dataset"
    confidence: str                # "verified" | "sourced"
    retrieved_at: str              # ISO8601 UTC
    apn: str = ""                  # parcel key when present
    situs_address: str = ""
    situs_zip: str = ""
    lat: float | None = None
    lng: float | None = None
    geo_precision: str = "jurisdiction"   # point | address | jurisdiction
    native_id: str = ""            # the source's own record id
    raw: dict = field(default_factory=dict)

    def as_row(self):
        d = asdict(self)
        d["raw"] = None  # raw kept out of the flat row; store separately as jsonb if wanted
        return d


@dataclass
class Quarantine:
    """A row that could not be emitted, with the reason — never silently dropped."""
    reason: str                    # no_source_url | blank_status | unmapped_status | error
    detail: str
    raw: dict


@dataclass
class RunReport:
    """Per-source run outcome. Mirrors HomeSignal's data_quality gate."""
    source_name: str
    considered: int = 0
    emitted: int = 0
    quarantined: int = 0
    excluded_blank_status: int = 0
    excluded_unmapped_status: int = 0
    unmapped_statuses: set = field(default_factory=set)   # surfaced for a human to map
    by_bucket: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)

    @property
    def data_quality(self) -> str:
        # 'pass' ONLY when the run produced at least one sourced signal; otherwise
        # 'coverage_coming' — never blank, never faked. (HomeSignal's exact gate.)
        return "pass" if self.emitted >= 1 else "coverage_coming"

    def summary(self) -> dict:
        return {
            "source": self.source_name,
            "data_quality": self.data_quality,
            "considered": self.considered,
            "emitted": self.emitted,
            "quarantined": self.quarantined,
            "excluded_blank_status": self.excluded_blank_status,
            "excluded_unmapped_status": self.excluded_unmapped_status,
            "unmapped_statuses": sorted(self.unmapped_statuses),
            "by_bucket": self.by_bucket,
            "by_type": self.by_type,
        }


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_signal_id(source_name: str, native_id: str, subject: str) -> str:
    """Deterministic id so re-runs are idempotent (upsert, not duplicate)."""
    key = f"{source_name}|{native_id}|{subject}".lower().strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
