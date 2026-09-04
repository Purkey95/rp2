# MonitorCLT 2.0

Public-records monitoring and entity resolution for real property. Starts with
Mecklenburg County, NC; built so the next county is a plugin.

**What it does:** watches the estates division, the parcel roll, the deed index,
foreclosure filings, the delinquent-tax list, code enforcement and the business
registry; keeps every
record's full history; links people across sources with a calibrated, explainable
resolver; puts the uncertain links in front of a reviewer whose decisions train the
model; ranks parcels by a transparent stack of public-record signals; and lets
nothing about a person leave the system except through a deny-by-default policy
gate, logged.

Pure Python standard library. SQLite is the reference store (everything here runs
and is tested with no services); `sql/postgres/` is the deployment DDL.

## Quickstart

```bash
cd monitorclt
export PYTHONPATH=src            # or: pip install -e .
export MONITORCLT_DB=monitorclt.db

python3 -m monitorclt init
python3 -m monitorclt contract --county MECKLENBURG --fixtures fixtures/mecklenburg --golden
python3 -m monitorclt ingest   --county MECKLENBURG --fixtures fixtures/mecklenburg
python3 -m monitorclt resolve
python3 -m monitorclt groups                # estate-centric review: every candidate per estate
python3 -m monitorclt review-queue          # pair queue, ordered by review value
python3 -m monitorclt serve                 # reviewer UI at http://127.0.0.1:8765/review
python3 -m monitorclt import-labels fixtures/mecklenburg/labels.csv
python3 -m monitorclt train
python3 -m monitorclt evaluate --target-precision 0.95
python3 -m monitorclt export --actor you --csv leads.csv
python3 -m monitorclt rank --limit 20
python3 -m monitorclt outcome declined --lead-id <lead> --by you   # suppresses on the spot
python3 -m monitorclt outcomes-report
python3 -m monitorclt run-daily --county MECKLENBURG --fixtures fixtures/mecklenburg
```

Operations, bringing a live source online, building a real labeled set, and splitting
into its own repository are in `docs/RUNBOOK.md`.

`--fixtures` serves recorded bodies instead of hitting the network. A live
deployment passes `--endpoint source=url` per connector (or configures it in the
county plugin); the parsers are identical because raw capture stores the exact
bytes and the fixtures *are* those bytes.

Run the tests with `python3 -m unittest discover -s tests -p "test_mclt_*.py"` (or
`pytest` from the repository root; they are collected by the existing workflow).

## How it is put together

```
fetch ──> raw_capture ──> parse ──> record_version ──> mention ──> entity_match ──> review
            (bytes,          (contract    (bitemporal,     (every name,   (features,      (decision =
             hash, url)       tested)      diff, events)    role, addr)    probability,    label = training
                                               │                            gates)         data)
                                               ▼
                                          event stream ──> watchlists ──> policy gate ──> webhook / digest / CSV / API
                                               │
                                               └──────────> signals (per-parcel stack) ──> rank
```

| Layer | Module | What it guarantees |
|---|---|---|
| Raw capture | `raw.py` | Every fetched body kept by hash; parsers are replayable. |
| Bitemporal history | `history.py` | Append-only versions with system time (`observed_at`/`superseded_at`) and valid time (`effective_date`); `as_of()` and `effective_as_of()`; every change is an event with a field diff. |
| Connectors | `sources/`, `counties/` | A `SourceSpec` declares keys, valid-time field, name fields and roles, address fields, parcel fields, event rules, retention. Transports are injected; `contract.py` holds parsers to golden fixtures. |
| Entities | `entities.py`, `normalize/` | Parcels (with lineage for splits/merges/renumbers), persons (created only on confirmation), mentions; names, component-parsed addresses, PINs, geometry. |
| Resolver | `resolve/` | One loop for every source pair. Named 0/1 evidence features → logistic model (seeded from the v1 rules, trained on labels, shrunk toward the seed) → hard gates → confirmed / pending / rejected. |
| Review | `review.py`, `ui/review.html`, `api.py` | Queue by probability, one candidate per screen, keyboard decisions; each decision is a label and an audit row. |
| Evaluation | `evaluate.py` | Two operating points, blocked-out vs scored-low, precision per evidence, threshold sweep, reliability table. |
| Policy | `policy.py` | Deny-by-default export: confirmed only, corroborated, not suppressed, inside retention, contact is the personal representative; fixed export fields; every decision in `export_log`; `why_do_you_have_this()`. |
| Outputs | `watch.py`, `signals.py`, `ids.py` | Watchlists (county / ZIP / event kind / value / polygon / radius), HMAC-signed webhooks, digests, stable lead ids, the parcel signal stack. |
| Quality | `quality.py` | Freshness, delta anomalies, parse failures, schema drift, blocked-out rate, rolling precision. |
| Persons | `persons.py` | Conservative clustering of a confirmed person's other mentions (deeds, tax, foreclosure) by shared address or parcel; a person page with every parcel and record. |
| Outcomes | `outcomes.py` | What happened after outreach; declines suppress immediately, "not in estate" becomes a negative signal, and a conversion report by signal and evidence. |
| Pipeline | `pipeline.py` | `run-daily`: ingest → resolve → cluster → watchlists → deliver → status, non-zero exit and an alert webhook on any problem. |

Read `ARCHITECTURE.md` for the reasoning behind each layer and what changed from v1.

## What it does not do

There is no incarceration, probation, parole or arrest table, field, feature, signal
or filter anywhere in this system, by design. Custody status is not a lead source,
not a lead score, not a distress label and not a segmentation field; it carries
fair-housing exposure and NC DAC's public data does not even cover county jails.
The policy layer cannot gate what the schema cannot hold.

Two guardrails live outside the code: run the workflow past a North Carolina
real-estate/probate attorney before operationalizing it, and keep solicitation of
estates within NC rules on contacting personal representatives. `confirmed` is a
records match, not a conclusion: verify chain of title, liens, heirs and the
representative's authority before any outreach.

## From v1 (`tools/monitorclt_probate`)

v1's matching logic is preserved as the resolver's *seed*: the same blocking rule,
the same evidence, the same "a name-only match can never confirm". The v2 resolver
reproduces v1's dispositions on v1's sample (`tests/test_mclt_resolve.py`) and adds
evidence v1 could not see (a related party on the owner string, a deed *to* the
decedent, street-level address agreement). v1's sample data and labels are the
fixtures here. Load v1-format JSONL directly:

```bash
python3 -m monitorclt ingest --county MECKLENBURG --fixtures ../tools/monitorclt_probate/sample \
    --source estate_case --source parcel --source deed --endpoint estate_case=fixture://estate_cases.jsonl
```

## Deploying on Postgres

`sql/postgres/001_core.sql` is the same schema with `bigserial`, `jsonb`,
`timestamptz` and `boolean`. Add PostGIS and a `geom` column on `parcel` (the file
shows how); the polygon/radius watchlist filters become `ST_Contains` /
`ST_DWithin`. The store layer is intentionally thin so a Postgres backend is a
second `Store` implementation, not a rewrite.
