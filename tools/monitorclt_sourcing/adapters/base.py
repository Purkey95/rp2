"""Base source adapter + the shared normalizer that enforces the five rules.

Design copied from HomeSignal: there is deliberately ZERO jurisdiction-specific
logic in the adapters. A new county/source is added by APPENDING an entry to the
jurisdiction registry (data), never by editing an adapter (code). One ArcGIS
adapter serves every ArcGIS layer; one Socrata adapter serves every Socrata
portal; the registry entry supplies the host, the column map, the type map, and
the status buckets.

`normalize_row` is where all five governance rules live, so every platform gets
identical guarantees. Adapters only know how to *fetch* rows and hand them here.
"""

from abc import ABC, abstractmethod

from provenance import (
    Signal, Quarantine, RunReport, BUCKETS, CONFIDENCE_BY_PRECISION,
    utcnow_iso, stable_signal_id,
)


def _get(row, ref):
    """Read a column reference from a row; '' if absent/None.

    Supports a dotted path into nested JSON (e.g. 'Address.FullAddress' for a
    Tyler CSS record) — but a flat key that exists wins first, so ordinary
    column names still work unchanged.
    """
    if ref is None:
        return ""
    if isinstance(row, dict) and ref in row:
        val = row.get(ref)
    elif "." in ref:
        val = row
        for part in ref.split("."):
            val = val.get(part) if isinstance(val, dict) else None
            if val is None:
                break
    else:
        val = None
    return "" if val is None else str(val).strip()


def _fill_template(template, row):
    """Fill {col} placeholders from the row; return None if any placeholder is empty."""
    out = template
    start = 0
    while "{" in out[start:]:
        i = out.index("{", start)
        j = out.index("}", i)
        col = out[i + 1:j]
        val = _get(row, col)
        if not val:
            return None
        out = out[:i] + val + out[j + 1:]
        start = i + len(val)
    return out


def resolve_source_url(entry, row):
    """Rule 1 (anti-fabrication) resolution order -> (url, precision) or (None, None).

      1. a column holding a record URL              -> precision "record"
      2. record_url_template with {col} filled       -> precision "record"
      3. the dataset landing page (dataset_url)       -> precision "dataset"
      4. nothing -> (None, None) => caller quarantines
    """
    cmap = entry.get("column_map", {})
    col = cmap.get("record_url")
    if col:
        v = _get(row, col)
        if v.startswith("http"):
            return v, "record"
    tmpl = entry.get("record_url_template")
    if tmpl:
        filled = _fill_template(tmpl, row)
        if filled:
            return filled, "record"
    dataset = entry.get("dataset_url")
    if dataset:
        return dataset, "dataset"
    return None, None


def normalize_row(entry, row):
    """Apply the five rules to one raw row. Returns a Signal or a Quarantine."""
    cmap = entry.get("column_map", {})

    # Rule 1 — ANTI-FABRICATION: no source URL => quarantine, never emit.
    url, precision = resolve_source_url(entry, row)
    if not url:
        return Quarantine("no_source_url", "row produced no record or dataset URL", row)

    # Rule 4 — NEVER GUESS THE BUCKET: exact status lookup; blank/unmapped excluded.
    # A statusless source (a tax-sale or demolition LIST, where every row is the
    # same actionable state) declares default_bucket instead of a status column —
    # this is a fixed, source-level assertion, not a per-row guess.
    status_col = cmap.get("status")
    if not status_col and entry.get("default_bucket"):
        status = entry.get("default_status_label", "listed")
        bucket = entry["default_bucket"]
    else:
        status = _get(row, status_col)
        if not status:
            return Quarantine("blank_status", "status field empty", row)
        # Collapse internal whitespace for the lookup (a double space in a county
        # status string is never semantically meaningful) — but keep the raw status
        # on the signal for provenance.
        def _norm(s):
            return " ".join(s.split()).lower()
        status_to_bucket = {_norm(k): v for k, v in entry.get("status_to_bucket", {}).items()}
        bucket = status_to_bucket.get(_norm(status))
        if bucket is None:
            return Quarantine("unmapped_status", status, row)
    if bucket not in BUCKETS:
        return Quarantine("unmapped_status", f"bucket '{bucket}' not canonical", row)

    # Rule 2 — NEVER GUESS CLASSIFICATION: type from explicit map, else "unclassified".
    type_map = {k.lower(): v for k, v in entry.get("type_map", {}).items()}
    type_raw = _get(row, cmap.get("type"))
    signal_type = type_map.get(type_raw.lower(), "unclassified") if type_raw else \
        entry.get("default_type", "unclassified")

    # Rule 3 — NEVER GUESS GEOGRAPHY: point only if lat/lng present; else jurisdiction.
    lat = lng = None
    geo_precision = "jurisdiction"
    lat_raw = _get(row, cmap.get("lat")) or _get(row, "__lat")
    lng_raw = _get(row, cmap.get("lng")) or _get(row, "__lng")
    if lat_raw and lng_raw:
        try:
            lat, lng = float(lat_raw), float(lng_raw)
            geo_precision = "point"
        except ValueError:
            lat = lng = None
    if lat is None and _get(row, cmap.get("situs_address")):
        geo_precision = "address"   # geocodable later; flagged, not faked here

    native_id = _get(row, cmap.get("native_id")) or _get(row, cmap.get("record_url")) or url
    apn = _get(row, cmap.get("apn"))
    situs = _get(row, cmap.get("situs_address"))
    subject = apn or situs or native_id

    return Signal(
        signal_id=stable_signal_id(entry["source_name"], native_id, subject),
        source_name=entry["source_name"],
        signal_type=signal_type,
        status=status,
        bucket=bucket,
        source_url=url,
        url_precision=precision,
        confidence=CONFIDENCE_BY_PRECISION[precision],
        retrieved_at=utcnow_iso(),
        apn=apn,
        situs_address=situs,
        situs_zip=_get(row, cmap.get("situs_zip")),
        lat=lat, lng=lng, geo_precision=geo_precision,
        native_id=native_id,
        raw=row,
    )


def normalize_rows(entry, rows):
    """Rule 5 — QUARANTINE, DON'T STOP. Returns (signals, quarantines, RunReport)."""
    report = RunReport(source_name=entry["source_name"])
    signals, quarantines = [], []
    for row in rows:
        report.considered += 1
        try:
            result = normalize_row(entry, row)
        except Exception as e:  # never let one bad row kill the run
            quarantines.append(Quarantine("error", repr(e), row))
            report.quarantined += 1
            continue
        if isinstance(result, Quarantine):
            quarantines.append(result)
            if result.reason == "blank_status":
                report.excluded_blank_status += 1
            elif result.reason == "unmapped_status":
                report.excluded_unmapped_status += 1
                report.unmapped_statuses.add(result.detail)
            else:
                report.quarantined += 1
            continue
        signals.append(result)
        report.emitted += 1
        report.by_bucket[result.bucket] = report.by_bucket.get(result.bucket, 0) + 1
        report.by_type[result.signal_type] = report.by_type.get(result.signal_type, 0) + 1
    return signals, quarantines, report


class SourceAdapter(ABC):
    """A platform connector. Subclasses implement fetch(); normalization is shared."""

    platform: str = "base"

    def __init__(self, fetch_json=None):
        # fetch_json(url) -> parsed JSON. Injected so tests run against fixtures
        # with zero network (HomeSignal's fixture-driven testing discipline).
        self._fetch_json = fetch_json

    @abstractmethod
    def fetch_rows(self, entry, zip_code=None):
        """Return an iterable of raw dict rows for one registry entry."""

    def run(self, entry, zip_code=None):
        rows = list(self.fetch_rows(entry, zip_code))
        return normalize_rows(entry, rows)
