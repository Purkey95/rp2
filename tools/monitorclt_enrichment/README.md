# MonitorCLT Contact Enrichment

Fills the contact gaps in the MonitorCLT database using parcel data you already
own, then queues what's left for paid skip tracing. This is the concrete start
of Phase 0 "contact enrichment" from the platform roadmap.

## Why this exists

~85% of active enrollments have no phone or email, so outreach steps skip them
(`sequence.contactable_pct` sits near 15% against a 50% target). The data to fix
most of that is already in hand — it just isn't **joined** to the contacts.
Parcel records carry the owner name and mailing address for essentially every
property; nothing was writing those onto the contact records.

## The two-step model

**Step 1 — the parcel join (this tool, free).** For every contact, find the
matching parcel and backfill owner name, mailing address, acreage, zoning, land
use, and an absentee flag. This converts "no-contact" records into **mailable**
records — a name plus a deliverable mailing address is everything a direct-mail
campaign needs, and mail is legal at any scale with no opt-in. This is the
cheapest, widest-blast-radius fix and it needs no new subscription.

**Step 2 — skip trace (separate, paid).** Feed the `skip_trace_queue.csv` this
tool emits (records that are mailable but still have no phone/email) to a
skip-trace API — BatchData / PropertyReach / Datafinder. Hit rate and cost are
both better because you're tracing complete identities (name + full address),
not fragments.

Run Step 1 first, always. Skip tracing blind is more expensive and less accurate.

## Direct mail: yes, the names make it work

The owner name + mailing address written by Step 1 is the mail-merge row. The
tool emits `mail_merge.csv` (deduped by mailing address) ready to hand to a mail
house or a Lob/Click2Mail API. The `absentee` flag is a bonus targeting lever:
owners whose mailing address differs from the property are the strongest
sell-likelihood segment in land — mail them first.

## Run it

```bash
python3 enrich.py \
  --contacts sample/contacts.csv \
  --parcels  sample/parcels.csv \
  --outdir   out
```

Outputs in `out/`:

| File | What it is |
|---|---|
| `enriched_contacts.csv` | every contact, with parcel fields merged in and `mailable` / `contactable` / `needs_skip_trace` flags |
| `mail_merge.csv` | mailable records (name + deliverable address), deduped — the direct-mail list |
| `skip_trace_queue.csv` | mailable but still no phone/email — the Step 2 input |
| `metrics.json` | MonitorCLT-style metric names and rates for the daily digest |

## Data contract

Wire your real tables to these column names (or edit the field maps at the top of
`enrich.py`). Missing columns are tolerated — the tool only uses what's present.

**Contacts (your CRM):** `contact_id`, `first_name`, `last_name`, `full_name`,
`situs_street`, `situs_city`, `situs_state`, `situs_zip` (the *property*
address), `mail_street`/`mail_city`/`mail_state`/`mail_zip` (owner mailing, if
known), `phone`, `email`, `apn`, `owner_name`, `source`.

**Parcels (Regrid export / Data Store):** `apn`, `situs_street`, `situs_city`,
`situs_state`, `situs_zip`, `owner_name`,
`mail_street`/`mail_city`/`mail_state`/`mail_zip` (owner mailing),
`acreage`, `zoning`, `land_use`.

## Matching strategy

Highest-confidence first; the first hit wins:

1. **APN exact** — both sides carry an assessor parcel number.
2. **Situs address + zip** — normalized (`123 North Main Street` → `123 n main st 28202`).
3. **Owner name + zip** — normalized and token-sorted; backfills where address
   entry is messy or the lead came in name-only.

Backfill never overwrites a value the CRM already has — it only fills blanks.

## Wiring to the live database

The `load_csv` / `write_csv` functions are the only I/O touch points. To run
against Postgres on the host, replace them with `psycopg` queries:

- `load_csv(contacts)` → `SELECT ... FROM contacts WHERE needs_enrichment`
- `load_csv(parcels)`  → `SELECT ... FROM parcels` (or the target county subset)
- `write_csv(enriched)` → `UPDATE contacts SET owner_name=..., mail_*=...,
  absentee=..., mailable=... WHERE contact_id=...`

Then register it as a standing `contact_enrichment` pipeline (nightly or on
new-lead insert) and emit the `metrics.json` values into MonitorCLT so
`enrichment.match_rate`, `enrichment.mailable_pct`, and the skip-trace queue
depth show up in the daily digest alongside everything else.

## Test

```bash
python3 test_normalize.py
```

Address/name normalization is the part most likely to silently regress; the test
pins the canonical-key behavior.
