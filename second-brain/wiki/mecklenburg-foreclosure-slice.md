---
type: synthesis
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, plan, mecklenburg, foreclosure]
---

# Plan: Mecklenburg foreclosure vertical slice (v0)

The recommended first build for [[monitorclt]], replacing the
obituary-first ordering of
[[life-event-property-intelligence-engine]]. Rationale in
[[design-review-life-event-engine]].

## Why this slice

- **Property-keyed** — the notice names the property, so
  [[entity-resolution-for-property-records]] is largely bypassed
  ([[property-keyed-vs-person-keyed-sources]]).
- **Real lifecycle** — foreclosure has the richest multi-stage progression,
  exercising [[event-lifecycle-state-machine]] properly.
- **Builds the index** that every person-keyed source later needs.
- **Cleanest legal footing** — legal notices are published for public
  notice ([[distressed-property-outreach-compliance]]).
- **Backtestable** — outcomes (sale, cancellation, redemption) are
  observable in the record.

## Step 0 — the blocking check (do first, before any code)

Confirm the terms of use for [[nc-ecourts]] portal access, county tax and
GIS bulk data, and [[nc-press-association-public-notices]] search/export.
The eCourts answer determines whether the probate branch is a scraper, a
license, or dead. Everything below is designed to survive a "no" on eCourts
by leaning on public notices and county sources.

## Step 1 — parcel + owner index — **DONE (2026-08-29)**

Built as `monitorclt/` in this repository: ArcGIS client with paging and
retry, address and owner normalization, SQLite index with provenance, and
person-to-parcel candidate generation. Full county load is 428,504 parcels in
~4 minutes; record and distinct-`pid` counts reconcile exactly against the
service. Buy-vs-build resolved in favour of build — see
[[mecklenburg-cama-parcel-data]] for the data's real shape and its traps.

Deliberately not included: any numeric score, and any auto-promotion of a
person match.

## Step 2 — two ingest paths

1. Foreclosure notices from [[nc-press-association-public-notices]]
2. County tax-foreclosure listings

Extract: case number, parcel/address, trustee/attorney, borrower name,
hearing date, sale date, upset bid deadline. Snapshot the raw document.

## Step 3 — event store

Bitemporal from day one: `event_date` + `observed_at`, per
[[event-lifecycle-state-machine]]. Event identity key `(county, case
number)` so notice + listing + court record merge into one event with three
observations. Raw source snapshots retained for audit.

## Step 4 — lifecycle

`filed -> notice of hearing -> authorized -> sale noticed -> auction ->
upset bid period -> final sale -> trustee deed`, plus terminal states
(cancelled, withdrawn, brought current, redeemed) and decay.

## Step 5 — ranked output, no score

A ranked list with an evidence trail per parcel. Explicitly **no numeric
opportunity score** in v0 — see
[[property-signal-scoring-and-calibration]].

## Step 6 — backtest

~100 known 2024-25 Mecklenburg foreclosure outcomes, replayed against
`observed_at`. Measure coverage, lead time, precision, rank quality. Compare
against the baselines (unranked notice list, tax-delinquency-by-amount, a
purchased vendor list). Only after this does a score get to exist.

## Explicitly out of scope for v0

Obituaries and probate; bankruptcy ([[pacer-cm-ecf]]); statewide or Tier 1
expansion; any numeric score; any outreach.

## Definition of done

A daily-refreshed, deduplicated, backtested ranked list of Mecklenburg
parcels in foreclosure, each traceable to a source document and a date, with
a measured false-positive rate.
