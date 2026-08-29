---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, mecklenburg, parcels, verified]
---

# Mecklenburg CAMA parcel data (ArcGIS)

The county's Computer Assisted Mass Appraisal ownership layer, published as an
ArcGIS REST feature service. The parcel spine for [[monitorclt]], implemented in
`monitorclt/monitorclt/parcel/`.

**Verified against the live service on 2026-08-29** — unlike most entries in
[[public-record-source-matrix-mecklenburg]], the facts below were measured, not
assumed.

## Access

- Layer: `TaxParcel_Camaownershipvalues/MapServer/0` on
  `meckgis.mecklenburgcountync.gov`
- **428,504 records**; `maxRecordCount` 2,000; pagination supported
- Full extract takes ~4 minutes over ~215 requests
- Requests without a `User-Agent` header are rejected with HTTP 403
- Sibling layers worth adding later: `TaxParcelSales`, `TaxParcel_camadata`,
  `VacantParcels`, `MasterAddressPoints`, `AccelaAllPermits`,
  `TaxParcelBoundaries`

**Terms of use are still unverified** — see
[[distressed-property-outreach-compliance]] before scheduling recurring
extracts.

## Fields that matter

Owner (`full_owner_name` plus split name columns and a secondary owner),
mailing address (`txt_mailaddr1`, `txt_city`, `txt_state`) — the absentee
signal; situs address; assessed land/building/total value; `dte_dateofsale`
(epoch milliseconds) and `amt_price` — ownership duration; deed book/page;
property use; acreage; municipality.

## Five traps, each measured

1. **`pid` is not unique.** 428,504 records carry only **396,310** distinct
   `pid` values: condominium units in a building share one. `camapid` and
   `propertyid` are both fully distinct. 210 N Church St alone holds 387
   parcels. Keying on `pid` silently collapses ~32,000 units.
2. **The county's first/last name split is unreliable.** It splits without
   understanding the string, so `'THE GELPI LIVING TRUST'` is stored as
   last=`'THE GELPI LIVING '` / first=`'TRUST'`. Classify from
   `full_owner_name` first; trust the split columns only afterwards.
3. **Substring matching is wrong more often than right.** `LIKE '%ESTATE%'`
   returns **1,119** "REAL ESTATE" companies against roughly **57** genuine
   estate owners — a ~95% false-positive rate. "LIFE" hits `GRACELIFE CHURCH`,
   `LIFELD MICHAEL A` and `ROADSTER LIFE LLC`; "ETAL" hits `VETAL DONALD III`
   and `METALS FREEDOM INC`. Match on word-boundary tokens, never substrings.
4. **`UNINC` sits in the jurisdiction slot** for unincorporated addresses
   (`' GOODMAN RD UNINC NC'`) on ~5.5% of parcels, and must be stripped like a
   city name.
5. **Street suffixes are not USPS.** The county writes AV, CR, BV, WY, PY, TR
   where USPS writes AVE, CIR, BLVD, WAY, PKWY, TRL — and TR is TRAIL here, not
   TERRACE. Both forms must canonicalize together or no cross-source address
   join works.

Two smaller ones: the state column carries `'NC '`, `'nc'` and `'NC   '`, so a
literal `txt_state <> 'NC'` filter miscounts **105** in-state parcels as
out-of-state; and **174** parcels mail to foreign addresses with empty city and
state columns, which read as owner-occupied unless handled explicitly.

## Signal populations (full county, 2026-08-29)

| Signal | Count | Note |
|---|---|---|
| Parcels | 428,504 | |
| Absentee (mail ≠ situs) | 166,965 | 39% |
| Out of state | 48,827 | 11.4% |
| Out of area | 66,430 | |
| International | 174 | |
| Company-owned | 82,985 | |
| Trust-owned | 3,032 | |
| **Decedent-marked** | **108** | ESTATE 75, HEIRS 31, LIFE ESTATE 2 |

The decedent-marked count is the important one, and it is **small**. The tax
roll already flags owners it knows to be deceased, requiring no obituary and no
[[entity-resolution-for-property-records]] — but 108 parcels countywide is a
one-time backlog sweep, not a pipeline. It does not replace the
obituary/probate path in [[life-event-property-intelligence-engine]]; it is a
free head start on it.

By contrast, absentee ownership at 167k parcels is a large, immediately
available, zero-matching signal — the cheapest real input the project has.
