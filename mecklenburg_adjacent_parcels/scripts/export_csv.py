"""Write the parcel lists: every parcel touching county/affiliate land, plus the county side."""
import csv, json, datetime
from collections import Counter, defaultdict

from classify import bucket
from landuse import CLASSES, land_class
from geomutil import esri_rings_to_polygon
from pyproj import Transformer

TO_WGS = Transformer.from_crs(2264, 4326, always_xy=True)
REL_RANK = ["shared boundary", "overlapping", "corner point", "within 1 ft"]
PRIVATE = "Private / other owner"

adj = json.load(open("adjacency.json"))
county = {f["attributes"]["objectid"]: f for f in json.load(open("county_parcels.json"))["features"]}
links = {int(k): v for k, v in adj["links"].items()}
attrs = {int(k): v for k, v in adj["attrs"].items()}
rings = {int(k): v for k, v in adj["rings"].items()}
county_oids = set(adj["county_oids"])
county_pids = {county[o]["attributes"]["pid"] for o in county_oids}


def money(v):
    return "" if v in (None, "") else f"{float(v):.0f}"


def date(v):
    if not v:
        return ""
    return datetime.datetime.utcfromtimestamp(v / 1000).strftime("%Y-%m-%d")


def clean(v):
    return " ".join(str(v).split()) if v not in (None, "") else ""


def geom_facts(ring_list):
    """CAMA's num_totalac is square feet for most parcels, so measure area off the geometry instead."""
    g = esri_rings_to_polygon(ring_list)
    p = g.representative_point()
    lon, lat = TO_WGS.transform(p.x, p.y)
    return lon, lat, g.area / 43560.0


records_per_pid = Counter(a["pid"] for noid, a in attrs.items() if noid in links)

rows = []
for noid, ls in links.items():
    a = attrs[noid]
    own = clean(a["full_owner_name"])
    grp = bucket(own) if (a["pid"] in county_pids or noid in county_oids) else None
    rels = {l["rel"] for l in ls}
    best = next(r for r in REL_RANK if r in rels)
    cgroups, cpids, cowners, cuses, ccats = set(), [], set(), set(), set()
    for l in ls:
        ca = county[l["county_oid"]]["attributes"]
        cgroups.add(bucket(ca["full_owner_name"]))
        cpids.append(ca["pid"])
        cowners.add(clean(ca["full_owner_name"]))
        cuses.add(clean(ca["txt_propertyuse_desc"]))
        ccats.add(CLASSES[land_class(ca["txt_propertyuse_desc"])])
    lon, lat, acres = geom_facts(rings[noid])
    rows.append({
        "pid": a["pid"],
        "owner": own,
        "owner_group": grp or PRIVATE,
        "owner_records_on_parcel": records_per_pid[a["pid"]],
        "situs_address": clean(a["situsaddress1"]),
        "municipality": clean(a["municipality_desc"]),
        "property_use": clean(a["txt_propertyuse_desc"]),
        "property_type": CLASSES[land_class(a["txt_propertyuse_desc"])],
        "acres": f"{acres:.3f}",
        "land_value": money(a["amt_landvalue"]),
        "total_value": money(a["amt_totalvalue"]),
        "last_sale_date": date(a["dte_dateofsale"]),
        "last_sale_price": money(a["amt_price"]),
        "mail_address": clean(f"{a['txt_mailaddr1'] or ''} {a['txt_mailaddr2'] or ''}"),
        "mail_city": clean(a["txt_city"]),
        "mail_state": clean(a["txt_state"]),
        "mail_zip": clean(a["txt_zipcode"]),
        "touch_type": best,
        "shared_boundary_ft": f"{sum(l['shared_ft'] for l in ls):.0f}",
        "county_groups_touched": "; ".join(sorted(cgroups)),
        "county_parcels_touched": len(ls),
        "county_pids_touched": "; ".join(sorted(set(cpids))[:8]) + (" …" if len(set(cpids)) > 8 else ""),
        "county_owners_touched": "; ".join(sorted(cowners)),
        "county_property_types": "; ".join(sorted(ccats)),
        "county_property_uses": "; ".join(sorted(u for u in cuses if u)),
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "polaris_url": f"https://polaris3g.mecklenburgcountync.gov/?pid={a['pid']}",
    })

rows.sort(key=lambda r: (r["owner_group"] != PRIVATE, r["municipality"], r["situs_address"], r["pid"]))
distinct_parcels = len({r["pid"] for r in rows})
with open("touching_parcels.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(f"touching_parcels.csv: {len(rows)} ownership records over {distinct_parcels} distinct parcels "
      f"({len(rows) - distinct_parcels} extra rows are condo/townhome units sharing a parcel)")
print(f"  not county-owned: {sum(1 for r in rows if r['owner_group'] == PRIVATE)} records / "
      f"{len({r['pid'] for r in rows if r['owner_group'] == PRIVATE})} parcels")
seen_area, acres_total = set(), 0.0
for r in rows:
    if r["pid"] not in seen_area:
        seen_area.add(r["pid"])
        acres_total += float(r["acres"])
print(f"  area (each parcel counted once): {acres_total:,.0f} acres")

# county side: one row per county/affiliate property with its neighbor count
nb_by_county = defaultdict(list)
for noid, ls in links.items():
    for l in ls:
        nb_by_county[l["county_oid"]].append(noid)
crows = []
for oid in sorted(county_oids):
    a = county[oid]["attributes"]
    nbs = nb_by_county.get(oid, [])
    outside = [n for n in nbs if attrs[n]["pid"] not in county_pids]
    crows.append({
        "county_pid": a["pid"],
        "owner": clean(a["full_owner_name"]),
        "owner_group": bucket(a["full_owner_name"]),
        "situs_address": clean(a["situsaddress1"]),
        "municipality": clean(a["municipality_desc"]),
        "property_use": clean(a["txt_propertyuse_desc"]),
        "property_type": CLASSES[land_class(a["txt_propertyuse_desc"])],
        "acres": f"{geom_facts(county[oid]['geometry']['rings'])[2]:.3f}",
        "total_value": money(a["amt_totalvalue"]),
        "touching_parcels": len(nbs),
        "touching_parcels_not_county_owned": len(outside),
        "polaris_url": f"https://polaris3g.mecklenburgcountync.gov/?pid={a['pid']}",
    })
crows.sort(key=lambda r: -r["touching_parcels"])
with open("county_properties.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(crows[0].keys()))
    w.writeheader()
    w.writerows(crows)
print("county_properties.csv", len(crows), "rows")
