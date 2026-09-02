# County source registry

`crossref.py` reads three record types and cares about which county each came
from. Nothing in the repo says where any of them live. This file is that
answer, per county — and it is the file Intake works from.

**It is intentionally a template with one worked row.** Filling it in requires
checking each county's current publication method and terms, which change; a
registry written from memory is worse than an empty one, because it looks
authoritative.

## Table to fill in, per county

| Field | Meaning |
|---|---|
| `county` | Value used in the `county` column throughout `../schema.sql`. Pick one spelling and never vary it — `county + file_number` and `county + pin` are unique keys, and a second spelling silently creates a second universe |
| `record_type` | `estate_case` \| `parcel` \| `deed` |
| `custodian` | Clerk of Superior Court (estates division) / assessor / Register of Deeds |
| `access_method` | Bulk download, open-data API, or portal search — in that order of preference |
| `endpoint` | Written as inline code, not a markdown link (see CI note below) |
| `publishes` | How often the source actually updates. Drives the routine, not the other way round |
| `name_format` | `first_last` or `last_first`, per `../match_rules.json` `name_formats`. Getting this wrong silently destroys blocking |
| `terms` | Terms of use, rate limits, robots policy, whether an account is required |
| `field_map` | Source column → `../schema.sql` column. The only place a rename is allowed to happen |
| `known_quirks` | Pagination limits, encoding, how the owner string handles multiple owners, suffix handling |

## Mecklenburg, and the statewide SOS — what is established, what is not

Verified in September 2026 from the custodians' public pages; endpoints and
column names are `TO VERIFY` until someone has the actual file in hand. Each row
below has a map in `intake/maps/` written against a fixture in `sample/intake/`.

| Field | Estate cases | Parcels | Deeds | Business entities |
|---|---|---|---|---|
| `county` | `MECKLENBURG` | `MECKLENBURG` | `MECKLENBURG` | — (statewide) |
| `record_type` | `estate_case` | `parcel` | `deed` | `business_entity` |
| `custodian` | Clerk of Superior Court, estates division (NC Judicial Branch) | County assessor / GIS | Register of Deeds | NC Secretary of State, Business Registration |
| `access_method` | **File only.** The eCourts Portal (Mecklenburg since 2023-10-09, all counties since 2025-10-13) prohibits automated access. Licensed route: NCAOC Remote Public Access program — online access and data extracts; whether estates are in an extract is `TO VERIFY` with NCAOC. Pre-2023-10 records by email request to the estates division | **Open data, free.** "Tax Parcel Ownership Data" on the county open-data site: owner name, mailing and situs address, assessed value | **Bulk index, sold by the ROD**, fees by volume and format | **Paid weekly CSV Data Subscription** ("Business Registration" contract): name, home state, addresses, domestic/foreign, officials, status, filings. Online search forbids scripted queries |
| `endpoint` | `nccourts.gov/services/remote-public-access-program` (`TO VERIFY`) | `maps.mecknc.gov/opendata` (`TO VERIFY`) | `TO VERIFY` — contact the ROD | `sosnc.gov/online_services/data_subscriptions` |
| `publishes` | Business days; lag `TO VERIFY` | `TO VERIFY` — assessor data changes slowly; weekly is plenty | Daily recording; weekly pull | Weekly |
| `name_format` | `first_last` | `last_first` | `last_first` | `first_last` (`TO VERIFY`; a comma form parses either way) |
| `terms` | Portal ToS forbid bots; RPA licence terms govern extracts | Open data licence `TO VERIFY` | ROD terms `TO VERIFY` | Subscription terms; no technical support offered |
| `field_map` | `intake/maps/estates.json` | `intake/maps/meck_parcels.json` | `intake/maps/meck_deeds.json` | `intake/maps/ncsos.json` (+ officials child file) |
| `known_quirks` | A mixed civil extract carries CVD/CVS/SP rows; the map keeps `^\d{2} E` only. Record the **personal representative** — `../README.md` step 1, and step 4 is addressed to that person | Two owner columns; joined with ` & ` so `crossref.py` can split them itself | PIN as printed, even when malformed | Registered agent stays on the entity row and is never merged into officials; it is usually a law office and never corroborates |

## Choosing an access method

Prefer, in order:

1. **Bulk download or open data.** Complete, stable, cheap, and its terms are
   usually explicit. County GIS and assessor data are commonly published this
   way.
2. **A documented API.** Same benefits, incremental pulls.
3. **Portal search.** Last resort. Rate-limited, layout-fragile, and the terms
   often prohibit automated access outright.

**Deterministic code parses; the agent orchestrates.** A model reading result
rows out of portal HTML is expensive, silently lossy, and the likeliest way a
fabricated field reaches `probate.*` — where it will be scored, and a fabricated
address is exactly what turns a name-only candidate into a false `confirmed`.
Where a portal is the only option, write an adapter that parses it and have
Intake run the adapter.

## Terms, before anything is automated

Check each source's terms of use and robots policy, record them in the row, and
respect the rate limits found there. Public record does not mean unlimited
automated retrieval, and a blocked IP costs the pipeline more than a slower
pull. Where terms are unclear, ask the custodian — clerks' offices answer this
question routinely.

This sits alongside, and does not replace, the review `../README.md` requires
from an NC real-estate/probate attorney before the workflow is operationalized.

## Coverage is measurable

`unmatched_estate_parcels` in every run report is the list of estate-marked
parcels with no matching estate case — in practice, the counties and date ranges
this registry is missing. If it grows, add a row here; do not tune the matcher.

---

**CI note:** write endpoints as inline code rather than markdown links.
`.github/workflows/documentation_check.yml` link-checks every markdown file in
the repo, and county portals commonly block the checker.
