# MonitorCLT Sourcing — provenance-enforced source adapters

Two HomeSignal patterns, ported to MonitorCLT and wired to run:

1. **Enforced provenance / anti-fabrication.** A signal that can't trace back to
   an official source record is *quarantined, never written*. Every stored signal
   carries `source_url`, `confidence`, and `retrieved_at`, and each source is
   graded `pass` / `coverage_coming` — never blank, never faked.
2. **Registry-driven source adapters.** One generic ArcGIS adapter and one Socrata
   adapter serve *every* county on those platforms. New coverage is one JSON entry
   in the registry (data), not a new scraper (code). This is the fix for the
   dead-lettered per-county scrapers (`rod_lending_ocr`, `stlco_taxsale`, etc.):
   county open-data portals expose the same records as stable APIs.

## Why this fixes a real problem

MonitorCLT's scrapers keep dying with exit-code-1 because each is bespoke HTML
scraping that breaks when a county tweaks a page. Most county permit / tax / code
data is actually published through **ArcGIS REST** and **Socrata SODA** — stable,
paginated JSON APIs. Point a generic adapter at them via the registry and the
source stops being brittle. And because provenance is enforced, a parse artifact
can never masquerade as a real delinquency.

## The five governance rules (in `adapters/base.normalize_row`)

1. **Anti-fabrication** — no `source_url` (record URL, or a filled
   `record_url_template`, or the dataset landing page) ⇒ quarantine.
2. **Never guess classification** — `signal_type` from an explicit `type_map`,
   else `unclassified`.
3. **Never guess geography** — precise `lat/lng` only if the row carries one;
   else `geo_precision='jurisdiction'`.
4. **Never guess the bucket** — `status` → lifecycle bucket is an exact lookup;
   an unmapped status is excluded **and surfaced** in the run report for a human
   to add to the registry.
5. **Quarantine, don't stop** — a bad row is logged and skipped; the run finishes
   and reports what it dropped and why.

## Run it (offline, against fixtures)

```bash
# Building permits (ArcGIS) — 3 sourced signals, 1 unmapped status surfaced
python3 run_source.py --registry jurisdictions.sample.json \
    --source meck-building-permits --fixture fixtures/arcgis_permits.json --outdir out/permits

# Code violations (Socrata) — quarantines the row with no resolvable URL
python3 run_source.py --registry jurisdictions.sample.json \
    --source charlotte-code-violations --fixture fixtures/socrata_code_violations.json --outdir out/cv

python3 test_sourcing.py   # pins all five rules
```

Each run writes `signals.jsonl`, `quarantine.jsonl` (with reasons), and
`report.json` (with the `data_quality` gate).

## Going live

The fixture flag is the only difference between a dry-run and production. Drop
`--fixture` and the adapter fetches the real endpoint in the registry entry:

```bash
python3 run_source.py --registry jurisdictions.prod.json --source meck-building-permits --zip 28202
```

Then:
1. Apply `schema.sql` to create the `signals` table (provenance constraints +
   the `source_coverage` view = the data_quality gate as SQL).
2. Upsert emitted signals by `signal_id` (idempotent — re-runs update, never dup).
3. Register each source run as a MonitorCLT pipeline and emit
   `source_coverage` into the daily digest, so a source flipping to
   `coverage_coming` (i.e. it stopped returning data) alerts you the way a
   dead-lettered scraper should have.

## Adding a county / source

Append one entry to the registry. No code changes. Minimum keys:

```jsonc
{
  "registry_id": "wake-building-permits",
  "platform": "arcgis",                       // or "socrata"
  "source_name": "Wake County Building Permits",
  "service_url": "https://maps.wake.gov/.../FeatureServer/0",   // arcgis
  "zip_field": "zip",
  "record_url_template": "https://permits.wake.gov/permit/{permit_number}",
  "column_map": { "status": "status", "type": "permit_type",
                  "apn": "parcel_id", "situs_address": "address", "native_id": "permit_number" },
  "status_to_bucket": { "Issued": "active", "Finaled": "resolved" },
  "type_map": { "BUILDING": "building_permit", "DEMOLITION": "demolition_permit" }
}
```

`registry.py` validates every entry at load — a source that couldn't produce a
URL for any row fails loudly instead of silently emitting nothing overnight.

## What the adapters can gather

Anything a county/city publishes on ArcGIS or Socrata, which is most of it:
**building / demolition / renovation permits**, **code-enforcement & vacancy
cases**, **zoning & rezoning cases**, **tax delinquency & tax-sale lists**,
**parcels & ownership**, **business licenses**, **foreclosure / lis pendens**
where counties post it. Each record links to its official page (the `source_url`),
which is where the permit's documents — site plans, approvals, inspections — live.
CSV/CKAN/RSS/EPA adapters slot in behind the same `SourceAdapter` interface when a
source uses one of those instead.
