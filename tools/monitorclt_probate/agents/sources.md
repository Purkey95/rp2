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

## Worked example — Mecklenburg estate cases

| Field | Value |
|---|---|
| `county` | `MECKLENBURG` |
| `record_type` | `estate_case` |
| `custodian` | Clerk of Superior Court, estates division |
| `access_method` | TO VERIFY — check for a bulk export before building against a portal |
| `endpoint` | TO VERIFY |
| `publishes` | TO VERIFY — estate filings are business-day; confirm the lag before setting the 06:00 routine |
| `name_format` | `first_last` (court order), per `../match_rules.json` |
| `terms` | TO VERIFY before any automated access |
| `field_map` | → `county`, `file_number`, `decedent_name`, `date_of_death`, `filing_date`, `case_status`, `personal_rep_name`, `pr_mailing_address`, `source_url`, `retrieved_at` |
| `known_quirks` | Record the **personal representative**, not only the decedent — `../README.md` step 1, and the rollup in `crossref.py` carries `personal_rep_name` because step 4 is addressed to that person |

Parcel and deed rows follow the same shape. Note that `../match_rules.json`
declares `parcel` and `deed` as `last_first` while `estate_case` is
`first_last`; if a county departs from that, the fix is a rules change proposed
through Calibrator, not a transformation hidden in intake.

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
