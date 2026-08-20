# Parcels touching Mecklenburg County land

Every tax parcel in Mecklenburg County, NC that physically touches a parcel owned by the County or
one of its affiliated public bodies, with the map and lists built from the County's own GIS.

Data pulled 20 Aug 2026 from Mecklenburg County GIS (`meckgis.mecklenburgcountync.gov`):
`TaxParcel_Camaownershipvalues` (parcel geometry + CAMA ownership/value), `StreetCenterline`,
`Jurisdictions`, `CountyBoundary`. 428,487 parcels countywide were searched.

## Results

| | parcels |
|---|---|
| County-side properties (the land being touched) | **3,158** |
| Parcels touching them | **20,171** |
| …of which **not** county/affiliate-owned | **17,495** |

Touching parcels total ~103,300 acres and ~$27.5B in assessed value.

**County-side owners, by group** — County government 2,771 · CMS Board of Education 266 ·
Hospital Authority (Atrium) 73 · Library / ABC / Landmarks 48.

**What each touching parcel touches** — County government 17,231 · CMS 2,806 · Hospital Authority 257 ·
Library / ABC / Landmarks 164. (A parcel can touch more than one group; the group is carried on every
row and every map feature.)

**Kind of contact** — shared boundary 18,034 · corner point 2,032 · within 1 ft 53 · overlapping 52.

**Jurisdiction** — Charlotte 16,065 · unincorporated county 1,498 · Huntersville 1,276 · Cornelius 568 ·
Matthews 308 · Mint Hill 259 · Pineville 119 · Davidson 53 · unassigned 25.

## Deliverables

- `map/mecklenburg_adjacent_parcels_map.html` — self-contained interactive map (no tile server, no CDN):
  filter by county owner group, jurisdiction and contact type, search by address/owner/PID, click any
  parcel for its record and a Polaris link.
- `data/touching_parcels.csv` — one row per touching parcel (20,171): owner, mailing address, situs
  address, use, acres, land and total value, last sale, contact type, shared boundary length, which
  county groups and county PIDs it touches, centroid lat/lon, Polaris URL.
- `data/county_properties.csv` — one row per county/affiliate property (3,158) with its neighbour counts.

## Who counts as "the County or an affiliate"

Owner names in CAMA are matched to four groups (`scripts/classify.py`):

| Group | Owner names |
|---|---|
| County government | MECKLENBURG COUNTY, COUNTY OF MECKLENBURG, Park & Recreation, Chronic Disease, Tax Collector |
| CMS (Board of Education) | (CHARLOTTE-)MECKLENBURG BOARD OF EDUCATION |
| Library / ABC / Landmarks | Public Library of Charlotte & Mecklenburg County, Mecklenburg County ABC Board, Charlotte-Mecklenburg Historic Landmarks Commission, County industrial/pollution-control financing authority |
| Hospital Authority (Atrium) | (THE) CHARLOTTE(-)MECKLENBURG HOSPITAL AUTHORITY |

Private owners whose names merely contain "Mecklenburg" or "Meck Co" — HOAs, churches, `MECKLENBURG
COUNTY LLC`, the Autism Society housing corporations, the Boy Scouts council — are excluded. The City of
Charlotte and its Housing Authority are **not** included; they are a different government.

## What "touching" means

Contact is measured on the parcel polygons in NC State Plane feet (EPSG:2264):

- **shared boundary** — boundaries overlap for at least 0.5 ft (the ordinary abutting neighbour).
- **corner point** — polygons meet only at a point.
- **within 1 ft** — a sliver gap in the parcel fabric, not a real separation.
- **overlapping** — polygons overlap by more than 1 sq ft (stacked condo/townhome parcels).

Public street right-of-way in Mecklenburg is generally not parcelled, so a parcel **across the street**
from county land does not touch it and is not in this list. That is the expansion to run when you want
surrounding rather than touching properties.

## Rerunning

```
pip install shapely pyproj
cd scripts
python fetch_county.py       # county/affiliate parcels + geometry
python fetch_neighbors.py    # every parcel within 1 ft of each of them (restartable, ~6 threads)
python analyze.py            # exact contact test -> adjacency.json
python export_csv.py         # the two CSVs
python build_map_data.py     # gzip+base64 payload for the map page
python validate.py           # re-derives neighbours for a random sample via an independent query
```

`validate.py` re-queries a random sample of county parcels by bounding box and recomputes contact from
scratch; the last run matched the pipeline on 8 of 8 parcels (0 missing, 0 extra).

## Caveats

- Ownership and values are CAMA snapshots; recent sales may not be reflected.
- Affiliate membership is decided by owner name, so a county holding recorded under an unusual name
  (a trust, a development authority) could be missed. The owner-name list actually present in the data
  is in `scripts/classify.py`.
- The map page needs a browser with `DecompressionStream` (Chrome/Edge 80+, Safari 16.4+, Firefox 113+).
