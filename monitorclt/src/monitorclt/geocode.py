"""Geocoding behind a cache, so an address is resolved once and compared as geometry.

Providers are pluggable. The static provider reads a JSON map (recorded results, or
a county address-point file joined offline); an HTTP provider for a licensed service
slots in behind the same `lookup`. Nothing here calls the network by default.
"""

from __future__ import annotations

import json
from typing import Dict, Optional, Tuple

from .normalize.addresses import parse_address
from .normalize.geo import Point
from .store import Store


class Geocoder:
    name = "null"

    def geocode(self, address_norm: str) -> Optional[Point]:  # pragma: no cover - abstract
        return None


class NullGeocoder(Geocoder):
    """Cache-only: answers from geocode_cache and never asks anyone."""


class StaticGeocoder(Geocoder):
    """Answers from a dict of normalized address -> (lat, lon); load one from JSON."""

    name = "static"

    def __init__(self, table: Optional[Dict[str, Tuple[float, float]]] = None) -> None:
        self.table = {parse_address(k).normalized: (float(v[0]), float(v[1])) for k, v in (table or {}).items()}

    @classmethod
    def from_json(cls, path: str) -> "StaticGeocoder":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def geocode(self, address_norm: str) -> Optional[Point]:
        return self.table.get(address_norm)


def lookup(store: Store, address: Optional[str], provider: Optional[Geocoder] = None) -> Optional[Point]:
    """Cache first; on a miss ask the provider and remember the answer (including a miss)."""
    if not address:
        return None
    norm = parse_address(address).normalized
    if not norm:
        return None
    row = store.one("SELECT lat, lon FROM geocode_cache WHERE address_norm = ?", (norm,))
    if row is not None:
        return (float(row["lat"]), float(row["lon"])) if row["lat"] is not None else None
    provider = provider or NullGeocoder()
    if isinstance(provider, NullGeocoder):
        return None
    pt = provider.geocode(norm)
    store.execute(
        "INSERT OR REPLACE INTO geocode_cache (address_norm, lat, lon, provider, geocoded_at) VALUES (?, ?, ?, ?, ?)",
        (norm, pt[0] if pt else None, pt[1] if pt else None, provider.name, store.now()),
    )
    return pt


def warm_from_parcels(store: Store) -> int:
    """Every parcel with coordinates is a free geocode for its own situs address."""
    rows = store.query("SELECT situs_norm, lat, lon FROM parcel WHERE situs_norm IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL")
    store.executemany(
        "INSERT OR IGNORE INTO geocode_cache (address_norm, lat, lon, provider, geocoded_at) VALUES (?, ?, ?, 'parcel', ?)",
        [(r["situs_norm"], r["lat"], r["lon"], store.now()) for r in rows],
    )
    store.commit()
    return len(rows)
