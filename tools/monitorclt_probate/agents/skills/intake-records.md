---
name: intake-records
description: Acquire estate case, parcel or deed records from a county source and normalize them to the JSONL crossref.py reads. Use when pulling new records for a county and date range.
---

# intake-records

Acquire records from one source in [`../sources.md`](../sources.md) and write
JSONL that `crossref.py` can read. One record per line, one file per record
type.

**This skill acquires and renames. It does not clean, infer, or improve.** A
field the source did not supply is absent. A field you are not sure of is
absent and reported. `crossref.py` scores what you hand it, and a plausible
address you filled in is exactly what turns a name-only candidate into a false
`confirmed`.

## Before you pull

1. Read the source's row in [`../sources.md`](../sources.md). If it has no row,
   stop and ask the human — do not improvise an endpoint.
2. Check `terms` in that row. Respect the rate limit written there.
3. Note `name_format`. It must agree with `../../match_rules.json`'s
   `name_formats` for this record type. If it does not, stop: a mismatch
   destroys blocking silently, and the fix is a rules proposal through
   Calibrator, not a transform here.

## Field contract

`../../schema.sql` is authoritative. Do not restate it — read it. It declares every
column, its type, whether it is nullable, and the natural unique key. Use those
exact column names as the JSONL keys.

Required on every record of every type, without exception:

- `county` — the spelling in the source's registry row and no other. `county +
  file_number` and `county + pin` are unique keys; a second spelling creates a
  second universe of records that will never match.
- `source_url` — the specific record, not the site root.
- `retrieved_at` — ISO 8601.

Then, per type, the `NOT NULL` columns of the matching table in `../../schema.sql`:
`estate_case` needs at minimum `file_number` and `decedent_name`; `parcel` needs
`pin` and `owner_name`; `deed` needs its book/page/instrument identity.

Dates are ISO `YYYY-MM-DD`. `assessed_value` is a bare number, not a currency
string. `owner_name` and `grantor_name` are the **verbatim** source strings —
`crossref.py` parses `PUBLIC JOHN Q & JANE R`, `ESTATE OF …`, suffixes and
organization tokens itself, and every "helpful" reformat you apply is a parse it
can no longer do correctly. `../../sample/` shows the shape.

## Per source

### Estate cases

Pull **newly opened** cases for a county and date range. Capture
`personal_rep_name` and `pr_mailing_address` — `../../README.md` step 1 is explicit
that the representative matters, step 4 is addressed to that person, and
`mailing_address_match` is one of the four corroborating signals that can lift a
candidate to `confirmed`. Dropping it costs real matches.

Set the date window from `publishes` in the registry row, with an overlap of at
least one publication cycle. Duplicates are handled by the unique key on load;
gaps are not handled at all.

### Parcels

Full owner string verbatim. `owner_mailing_address` is what
`mailing_address_match` compares against, and `situs_address` is what
`situs_address_match` compares against — both are corroboration, so an absent
one costs evidence and a wrong one manufactures it.

### Deeds

`grantor_name`, `recorded_date` and `parcel_pin` as printed on the instrument,
including when the PIN is malformed. `deed_grantor_link` corroboration and the
`post_death_conveyance` flag both depend on `recorded_date` being accurate; a
deed from the decedent recorded after the date of death is the signal that a
parcel may already have left the estate.

### Business entities (NC Secretary of State)

Statewide, so no `county`. Two subscription files — the entity table and the
officials table — joined on SOS id; the map nests officials under each entity.
Keep the registered agent on the entity record and **never** fold it into
officials: the matcher scores an agent link weaker than an official link on
purpose, because an agent is usually the entity's attorney. Entity names and
official names verbatim — `crossref.normalize_entity_name` and `parse_name` do
the normalizing, and a name you tidied is a key that no longer meets its parcel.

This is the source that makes an LLC-held parcel reachable at all, and also the
one whose real column layout nobody has seen yet: `intake/maps/ncsos.json` is
written against `sample/intake/ncsos_*.csv` and marked `TO VERIFY`.

## The engine does the parsing

Do not read a CSV yourself. `intake/adapt.py` reads it, applies the map, types
the fields, stamps `county` / `source_url` / `retrieved_at`, validates, and
writes the good rows and the rejected rows apart:

```bash
python3 intake/adapt.py --input <vendor>.csv --map intake/maps/<source>.json \
        --out <type>.jsonl --county MECKLENBURG --source-url-base <url>
```

A non-zero exit means something was rejected. Report it; do not re-run with
`--allow-rejects` to make it green. When the vendor's columns differ from the
fixture, the fix is the map file, and a human applies it.

## Validate before writing

Per record: required columns present and non-empty; `county` matches the
registry spelling exactly; dates parse as ISO; numerics are numeric; no key
outside `../../schema.sql`'s column list for that table.

Per file: at least one record; no duplicate natural key within the file; the
count matches what the source reported, if it reports one.

**A validation failure is an error you raise, not a record you fix.** Write the
good records, list the rejected ones with the reason, and report both. Never
partially write a file and report success — the run is chained to intake
succeeding, and a half-file produces a queue of `pending` rows whose real cause
is a broken pull.

## Verify before reporting done

Pick five records at random, open their `source_url`, and compare every field
against what the page says. Then report: counts written, counts rejected with
reasons, the date range covered, and the five you checked. Hand over your
fourth draft, not your first.

## Coverage

`unmatched_estate_parcels` in the last run report lists estate-marked parcels
with no matching estate case — each names a county, and usually a date range,
that has not been pulled. Work that list; it is intake's backlog, and it is
generated for free by every run.
