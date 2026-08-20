"""Pack parcels, county land and context layers into one gzipped payload for the map page."""
import base64, gzip, json, math
from collections import defaultdict

from classify import bucket
from landuse import CLASSES, land_class
from geomutil import esri_rings_to_polygon
from pyproj import Transformer
from shapely.geometry import LineString

TO_MERC = Transformer.from_crs(2264, 3857, always_xy=True)
Q = 4                      # quantization steps per metre (0.25 m)
SIMPLIFY_FT = 1.5
REL_RANK = ["shared boundary", "overlapping", "corner point", "within 1 ft"]
GROUPS = ["County government", "CMS (Board of Education)", "Library / ABC / Landmarks", "Hospital Authority (Atrium)"]
GIDX = {g: i for i, g in enumerate(GROUPS)}

adj = json.load(open("adjacency.json"))
county_feats = {f["attributes"]["objectid"]: f for f in json.load(open("county_parcels.json"))["features"]}
links = {int(k): v for k, v in adj["links"].items()}
attrs = {int(k): v for k, v in adj["attrs"].items()}
rings = {int(k): v for k, v in adj["rings"].items()}
county_oids = set(adj["county_oids"])
county_pids = {county_feats[o]["attributes"]["pid"] for o in county_oids}


class Dict_:
    """Small string dictionary so repeated values (uses, municipalities) cost one int."""

    def __init__(self):
        self.vals, self.idx = [], {}

    def __call__(self, s):
        s = " ".join(str(s or "").split())
        if s not in self.idx:
            self.idx[s] = len(self.vals)
            self.vals.append(s)
        return self.idx[s]


def encode_geom(geom, tol=SIMPLIFY_FT):
    """Shapely polygon (ft, EPSG:2264) -> list of delta-encoded integer rings in Web Mercator."""
    simplified = geom.simplify(tol, preserve_topology=False)
    if not simplified.is_empty:
        geom = simplified
    if geom.is_empty:
        return None
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            xs, ys = zip(*list(ring.coords))
            mx, my = TO_MERC.transform(xs, ys)
            enc, px, py = [], 0, 0
            for x, y in zip(mx, my):
                ix, iy = int(round(x * Q)), int(round(y * Q))
                if enc and ix == px and iy == py:
                    continue
                enc.extend((ix - px, iy - py) if enc else (ix, iy))
                px, py = ix, iy
            if len(enc) >= 8:
                out.append(enc)
    if not out and tol > 0:
        return encode_geom(geom, 0)   # tiny sliver: keep it at full resolution
    return out or None


def encode_line(paths):
    out = []
    for path in paths:
        line = LineString([(p[0], p[1]) for p in path]).simplify(20)
        xs, ys = zip(*list(line.coords))
        mx, my = TO_MERC.transform(xs, ys)
        enc, px, py = [], 0, 0
        for x, y in zip(mx, my):
            ix, iy = int(round(x * Q)), int(round(y * Q))
            enc.extend((ix - px, iy - py) if enc else (ix, iy))
            px, py = ix, iy
        if len(enc) >= 4:
            out.append(enc)
    return out


use_d, muni_d, cown_d, own_d = Dict_(), Dict_(), Dict_(), Dict_()
P = defaultdict(list)
for noid in sorted(links):
    a, ls = attrs[noid], links[noid]
    g = esri_rings_to_polygon(rings[noid])
    geo = encode_geom(g) if g else None
    if not geo:
        continue
    rels = {l["rel"] for l in ls}
    mask = 0
    cowners, cpids = set(), set()
    cmask = 0
    for l in ls:
        ca = county_feats[l["county_oid"]]["attributes"]
        b = bucket(ca["full_owner_name"])
        mask |= 1 << GIDX[b]
        cmask |= 1 << land_class(ca["txt_propertyuse_desc"])
        cowners.add(" ".join(county_feats[l["county_oid"]]["attributes"]["full_owner_name"].split()))
        cpids.add(county_feats[l["county_oid"]]["attributes"]["pid"])
    own = " ".join((a["full_owner_name"] or "").split())
    self_group = bucket(own) if (a["pid"] in county_pids or noid in county_oids) else None
    P["pid"].append(a["pid"])
    P["own"].append(own_d(own))
    P["addr"].append(" ".join((a["situsaddress1"] or "").split()))
    P["muni"].append(muni_d(a["municipality_desc"]))
    P["use"].append(use_d(a["txt_propertyuse_desc"]))
    P["ac"].append(int(round(g.area / 43560.0 * 100)))
    P["val"].append(int(a["amt_totalvalue"] or 0))
    P["rel"].append(REL_RANK.index(next(r for r in REL_RANK if r in rels)))
    P["sft"].append(int(round(sum(l["shared_ft"] for l in ls))))
    P["grp"].append(mask)
    P["nct"].append(len(ls))
    P["cown"].append([cown_d(c) for c in sorted(cowners)])
    P["cpid"].append(sorted(cpids)[:6])
    P["self"].append(-1 if self_group is None else GIDX[self_group])
    P["cat"].append(land_class(a["txt_propertyuse_desc"]))
    P["ccat"].append(cmask)
    P["geo"].append(geo)
print("touching parcels encoded:", len(P["pid"]))

C = defaultdict(list)
for oid in sorted(county_oids):
    f = county_feats[oid]
    a = f["attributes"]
    g = esri_rings_to_polygon(f["geometry"]["rings"])
    geo = encode_geom(g) if g else None
    if not geo:
        continue
    C["pid"].append(a["pid"])
    C["own"].append(own_d(" ".join(a["full_owner_name"].split())))
    C["addr"].append(" ".join((a["situsaddress1"] or "").split()))
    C["use"].append(use_d(a["txt_propertyuse_desc"]))
    C["muni"].append(muni_d(a["municipality_desc"]))
    C["ac"].append(int(round(g.area / 43560.0 * 100)))
    C["grp"].append(GIDX[bucket(a["full_owner_name"])])
    C["cat"].append(land_class(a["txt_propertyuse_desc"]))
    C["geo"].append(geo)
print("county parcels encoded:", len(C["pid"]))

streets = [enc for f in json.load(open("ctx_streets.json")) for enc in encode_line(f["geometry"]["paths"])]
juris = []
for f in json.load(open("ctx_juris.json")) + json.load(open("ctx_county.json")):
    g = esri_rings_to_polygon(f["geometry"]["rings"])
    if g:
        e = encode_geom(g)
        if e:
            juris.extend(e)
print("street paths:", len(streets), "boundary rings:", len(juris))

payload = {
    "q": Q, "groups": GROUPS, "rels": REL_RANK, "classes": CLASSES,
    "dicts": {"use": use_d.vals, "muni": muni_d.vals, "cown": cown_d.vals, "own": own_d.vals},
    "parcels": dict(P), "county": dict(C), "streets": streets, "boundaries": juris,
}
raw = json.dumps(payload, separators=(",", ":")).encode()
b64 = base64.b64encode(gzip.compress(raw, 9)).decode()
open("map_payload.b64", "w").write(b64)
print(f"raw {len(raw)/1e6:.1f} MB -> gzip+base64 {len(b64)/1e6:.2f} MB")
