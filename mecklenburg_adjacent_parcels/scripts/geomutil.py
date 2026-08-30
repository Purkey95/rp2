"""Esri JSON ring -> shapely conversion (NC State Plane feet, EPSG:2264)."""
from shapely.geometry import Polygon
from shapely.ops import unary_union


def _shoelace(ring):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += x1 * y2 - x2 * y1
    return a / 2.0


def esri_rings_to_polygon(rings):
    """Esri exterior rings are clockwise (negative shoelace); holes are counter-clockwise."""
    outers, holes = [], []
    for ring in rings:
        if len(ring) < 4:
            continue
        p = Polygon([(pt[0], pt[1]) for pt in ring])
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            continue
        (holes if _shoelace(ring) > 0 else outers).append(p)
    if not outers:
        return None
    geom = unary_union(outers)
    if holes:
        geom = geom.difference(unary_union(holes))
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom if not geom.is_empty else None
