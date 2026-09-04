"""Just enough geometry to run boundary watchlists without PostGIS.

With PostGIS available, the same questions become ST_Contains / ST_DWithin on a
geometry column (see sql/postgres/001_core.sql). These pure-python versions keep
the SQLite reference store honest and the tests runnable anywhere.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]  # (lat, lon)

EARTH_RADIUS_M = 6371008.8


def haversine_m(a: Point, b: Point) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def within_m(a: Optional[Point], b: Optional[Point], meters: float) -> bool:
    if a is None or b is None:
        return False
    return haversine_m(a, b) <= meters


def bbox(points: Iterable[Point]) -> Tuple[float, float, float, float]:
    pts = list(points)
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return min(lats), min(lons), max(lats), max(lons)


def in_bbox(p: Optional[Point], box: Sequence[float]) -> bool:
    if p is None:
        return False
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def point_in_polygon(p: Optional[Point], polygon: Sequence[Point]) -> bool:
    """Ray casting on (lat, lon) pairs. Good enough for county-scale boundaries."""
    if p is None or len(polygon) < 3:
        return False
    x, y = p[1], p[0]
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def centroid(polygon: Sequence[Point]) -> Point:
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def as_point(lat: Optional[float], lon: Optional[float]) -> Optional[Point]:
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def parse_points(value: Sequence[Sequence[float]]) -> List[Point]:
    return [(float(p[0]), float(p[1])) for p in value]
