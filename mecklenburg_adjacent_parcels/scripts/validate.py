"""Independent check: re-derive neighbours for a sample of county parcels via an envelope query."""
import json, random
from arc import query
from geomutil import esri_rings_to_polygon

LAYER = "https://meckgis.mecklenburgcountync.gov/server/rest/services/TaxParcel_Camaownershipvalues/FeatureServer/0"
adj = json.load(open("adjacency.json"))
links = {int(k): v for k, v in adj["links"].items()}
county = {f["attributes"]["objectid"]: f for f in json.load(open("county_parcels.json"))["features"]}
by_county = {}
for noid, ls in links.items():
    for l in ls:
        by_county.setdefault(l["county_oid"], set()).add(noid)

random.seed(7)
sample = random.sample(sorted(set(adj["county_oids"])), 8)
bad = 0
for oid in sample:
    g = esri_rings_to_polygon(county[oid]["geometry"]["rings"])
    minx, miny, maxx, maxy = g.bounds
    env = {"xmin": minx - 50, "ymin": miny - 50, "xmax": maxx + 50, "ymax": maxy + 50,
           "spatialReference": {"wkid": 2264}}
    feats, off = [], 0
    while True:
        d = query(LAYER, geometry=json.dumps(env), geometryType="esriGeometryEnvelope", inSR=2264, outSR=2264,
                  spatialRel="esriSpatialRelIntersects", outFields="objectid,pid,full_owner_name",
                  returnGeometry="true", resultOffset=off, resultRecordCount=1000)
        got = d.get("features", [])
        feats.extend(got)
        if len(got) < 1000:
            break
        off += 1000
    truth = set()
    for f in feats:
        n = f["attributes"]["objectid"]
        if n == oid:
            continue
        og = esri_rings_to_polygon(f.get("geometry", {}).get("rings", []))
        if og is not None and g.distance(og) <= 1.0:
            truth.add(n)
    got_set = by_county.get(oid, set())
    miss, extra = truth - got_set, got_set - truth
    flag = "OK " if not miss and not extra else "DIFF"
    if miss or extra:
        bad += 1
    print(f"{flag} county oid {oid} pid {county[oid]['attributes']['pid']}: pipeline {len(got_set)}, "
          f"envelope check {len(truth)}, missing {len(miss)}, extra {len(extra)}")
print("mismatched parcels:", bad, "of", len(sample))
