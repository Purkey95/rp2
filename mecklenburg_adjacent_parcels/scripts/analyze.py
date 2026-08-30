"""Decide which parcels actually touch each county-owned parcel and roll the results up."""
import json, sys
from collections import defaultdict

from classify import bucket
from geomutil import esri_rings_to_polygon

EDGE_MIN_FT = 0.5      # shared boundary shorter than this is treated as a corner touch
GAP_TOL_FT = 1.0       # sliver gaps in the parcel fabric still count as touching
OVERLAP_MIN_SQFT = 1.0

county = {f["attributes"]["objectid"]: f for f in json.load(open("county_parcels.json"))["features"]}
county_buckets = {oid: bucket(f["attributes"]["full_owner_name"]) for oid, f in county.items()}
county_oids = {oid for oid, b in county_buckets.items() if b}
county_pids = {county[o]["attributes"]["pid"] for o in county_oids}

nb_attrs, nb_geom_src = {}, {}          # objectid -> attributes / rings
links = defaultdict(list)               # neighbor objectid -> list of link records
county_geoms = {}
processed = skipped = 0

for line in open("neighbors.jsonl"):
    rec = json.loads(line)
    coid = rec["county_oid"]
    if coid not in county_oids or "neighbors" not in rec:
        continue
    cgeom = esri_rings_to_polygon(county[coid]["geometry"]["rings"])
    if cgeom is None:
        skipped += 1
        continue
    county_geoms[coid] = cgeom
    cbound = cgeom.boundary
    for f in rec["neighbors"]:
        a = f["attributes"]
        noid = a["objectid"]
        if noid == coid:
            continue
        g = esri_rings_to_polygon(f.get("geometry", {}).get("rings", []))
        if g is None:
            continue
        shared_ft = 0.0
        try:
            inter = cbound.intersection(g.boundary)
            shared_ft = inter.length
            overlap = cgeom.intersection(g).area
        except Exception:  # noqa: BLE001 - topology edge cases
            g2 = g.buffer(0)
            inter = cbound.intersection(g2.boundary)
            shared_ft, overlap = inter.length, cgeom.intersection(g2).area
        gap = cgeom.distance(g)
        if overlap > OVERLAP_MIN_SQFT and shared_ft < EDGE_MIN_FT:
            rel = "overlapping"
        elif shared_ft >= EDGE_MIN_FT:
            rel = "shared boundary"
        elif gap <= 0.0001:
            rel = "corner point"
        elif gap <= GAP_TOL_FT:
            rel = "within 1 ft"
        else:
            continue
        nb_attrs[noid] = a
        nb_geom_src[noid] = f["geometry"]["rings"]
        links[noid].append({"county_oid": coid, "rel": rel, "shared_ft": round(shared_ft, 1),
                            "gap_ft": round(gap, 2), "overlap_sqft": round(overlap, 1)})
    processed += 1
    if processed % 250 == 0:
        print(f"  {processed} county parcels analyzed, {len(links)} touching parcels so far", file=sys.stderr, flush=True)

print(f"county parcels analyzed: {processed} (geometry skipped: {skipped})")
print(f"distinct touching parcels (incl. county-owned): {len(links)}")
priv = [n for n in links if nb_attrs[n]["pid"] not in county_pids and n not in county_oids]
print(f"  of which NOT county/affiliate-owned: {len(priv)}")
json.dump({"county_oids": sorted(county_oids),
           "links": {str(k): v for k, v in links.items()},
           "attrs": {str(k): v for k, v in nb_attrs.items()},
           "rings": {str(k): v for k, v in nb_geom_src.items()}},
          open("adjacency.json", "w"))
