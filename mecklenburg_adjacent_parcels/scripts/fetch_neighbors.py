"""For every county-owned parcel, pull every parcel that touches it (within a small tolerance).

Results are cached to neighbors.jsonl (one line per county parcel) so the run is restartable.
"""
import json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor
from arc import query

LAYER = "https://meckgis.mecklenburgcountync.gov/server/rest/services/TaxParcel_Camaownershipvalues/FeatureServer/0"
FIELDS = ("objectid,pid,full_owner_name,txt_mailaddr1,txt_mailaddr2,txt_city,txt_state,txt_zipcode,"
          "situsaddress1,txt_propertyuse_desc,num_totalac,amt_totalvalue,amt_landvalue,"
          "municipality_desc,txt_legaldesc,dte_dateofsale,amt_price")
TOL_FEET = 1.0          # slack for slivers/rounding in the parcel fabric
PAGE = 1000

out_lock = threading.Lock()
done = set()
if os.path.exists("neighbors.jsonl"):
    for line in open("neighbors.jsonl"):
        try:
            done.add(json.loads(line)["county_oid"])
        except Exception:  # noqa: BLE001 - tolerate a truncated last line
            pass
out = open("neighbors.jsonl", "a")
progress = [0]


def neighbors_of(feat):
    oid = feat["attributes"]["objectid"]
    geom = feat["geometry"]
    rings = geom.get("rings", [])
    params = dict(
        geometry=json.dumps({"rings": rings, "spatialReference": {"wkid": 2264}}),
        geometryType="esriGeometryPolygon",
        inSR=2264, outSR=2264,
        spatialRel="esriSpatialRelIntersects",
        distance=TOL_FEET, units="esriSRUnit_Foot",
        outFields=FIELDS, returnGeometry="true",
    )
    feats, off = [], 0
    while True:
        d = query(LAYER, resultOffset=off, resultRecordCount=PAGE, **params)
        got = d.get("features", [])
        feats.extend(got)
        if len(got) < PAGE:
            break
        off += PAGE
    return oid, feats


def work(feat):
    oid = feat["attributes"]["objectid"]
    if oid in done:
        return
    try:
        oid, feats = neighbors_of(feat)
    except Exception as e:  # noqa: BLE001 - record and continue
        with out_lock:
            out.write(json.dumps({"county_oid": oid, "error": str(e)[:300]}) + "\n")
            out.flush()
        return
    with out_lock:
        out.write(json.dumps({"county_oid": oid, "neighbors": feats}) + "\n")
        out.flush()
        progress[0] += 1
        if progress[0] % 25 == 0:
            print(f"{progress[0]} county parcels processed", file=sys.stderr, flush=True)


def main():
    county = json.load(open("county_parcels.json"))["features"]
    todo = [f for f in county if f["attributes"]["objectid"] not in done]
    print(f"{len(county)} county parcels, {len(todo)} to fetch", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, todo))
    print("done", flush=True)


if __name__ == "__main__":
    main()
