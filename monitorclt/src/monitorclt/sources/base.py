from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .transport import Transport

EventRule = Callable[[Optional[Dict[str, Any]], Dict[str, Any]], List[Tuple[str, Optional[str], Dict[str, Any]]]]


@dataclass
class SourceSpec:
    """Everything the rest of the system needs to know about a source, declaratively."""

    name: str
    mode: str = "snapshot"  # snapshot: absent rows are retired | incremental: rows only accumulate
    key_fields: Tuple[str, ...] = ("id",)
    effective_field: Optional[str] = None
    name_format: str = "last_first"
    mention_roles: Dict[str, str] = field(default_factory=dict)  # payload field -> role
    address_fields: Dict[str, str] = field(default_factory=dict)  # payload field -> kind
    parcel_fields: Tuple[str, ...] = ()  # payload fields carrying a PIN
    required_fields: Tuple[str, ...] = ()
    event_rules: List[EventRule] = field(default_factory=list)
    retention_days: int = 3650
    description: str = ""

    def natural_key(self, record: Dict[str, Any]) -> str:
        parts = []
        for f in self.key_fields:
            v = record.get(f)
            parts.append("" if v is None else str(v).strip().upper())
        return "|".join(parts)

    def effective_date(self, record: Dict[str, Any]) -> Optional[str]:
        if not self.effective_field:
            return None
        v = record.get(self.effective_field)
        return str(v)[:10] if v else None

    def missing_required(self, record: Dict[str, Any]) -> List[str]:
        return [f for f in self.required_fields if record.get(f) in (None, "")]


@dataclass
class Fetched:
    body: bytes
    url: Optional[str] = None
    content_type: Optional[str] = None
    watermark: Optional[str] = None  # value to persist once this batch is committed


class Connector:
    """Subclass per source. fetch() yields raw bodies; parse() turns one body into records."""

    spec: SourceSpec
    county: str

    def __init__(self, county: str) -> None:
        self.county = county

    @property
    def name(self) -> str:
        return self.spec.name

    def fetch(self, transport: Transport, watermark: Optional[str]) -> Iterable[Fetched]:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:  # pragma: no cover - abstract
        raise NotImplementedError

    def clean(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for per-source normalization before storage (trim, retype). Default: strip strings."""
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in record.items()}


# --------------------------------------------------------- parse helpers ----


def parse_jsonl(body: bytes) -> List[Dict[str, Any]]:
    text = body.decode("utf-8-sig").strip()
    if not text:
        return []
    if text[0] == "[":
        return list(json.loads(text))
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            out.append(json.loads(line))
    return out


def parse_csv(body: bytes) -> List[Dict[str, Any]]:
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or "").strip(): v for k, v in row.items()} for row in reader]


# ------------------------------------------------------------- registry -----


class Registry:
    """County plugins register the connectors they provide, under a profile.

    The "default" profile is what `ingest --county X` uses. A county can also expose
    alternative profiles ("live" for the real endpoints, "sample" for synthetic data)
    that produce records for the same county name.
    """

    def __init__(self) -> None:
        self._counties: Dict[Tuple[str, str], Callable[..., List[Connector]]] = {}

    def register_county(self, county: str, factory: Callable[..., List[Connector]], profile: str = "default") -> None:
        self._counties[(county.upper(), profile)] = factory

    def counties(self) -> List[str]:
        return sorted({c for c, _ in self._counties})

    def profiles(self, county: str) -> List[str]:
        return sorted(p for c, p in self._counties if c == county.upper())

    def connectors(self, county: str, profile: str = "default", endpoints: Optional[Dict[str, str]] = None) -> List[Connector]:
        try:
            factory = self._counties[(county.upper(), profile)]
        except KeyError:
            raise KeyError("no county plugin for {0!r} profile {1!r}; known: {2}".format(county, profile, sorted(self._counties)))
        return factory(endpoints) if endpoints is not None else factory()

    def connector(self, county: str, source: str, profile: str = "default") -> Connector:
        for c in self.connectors(county, profile):
            if c.name == source:
                return c
        raise KeyError("county {0} profile {1} has no source {2!r}".format(county, profile, source))


registry = Registry()
