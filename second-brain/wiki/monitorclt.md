---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, real-estate, charlotte]
---

# MonitorCLT

A Charlotte-area (Mecklenburg County, NC) real-estate intelligence system.

## Current state

As of 2026-08-29 this vault holds **no MonitorCLT source code** — the `rp2`
repository this wiki lives in is an unrelated crypto tax calculator. Every
page here describes design intent, not a running implementation.

## Original design

News/RSS ingestion scored with sentiment analysis. Superseded by the
proposal in [[life-event-property-intelligence-engine]], which replaces
sentiment with structured public-record event extraction.

## Intended shape

- **Ingest** public records and news across NC (see
  [[public-record-source-matrix-mecklenburg]])
- **Resolve** people, LLCs and addresses to parcels
  ([[entity-resolution-for-property-records]])
- **Track** each parcel through a distress/transition lifecycle
  ([[event-lifecycle-state-machine]])
- **Rank** parcels by likelihood of near-term ownership transition
  ([[property-signal-scoring-and-calibration]])

## Open decisions

- Build order — proposal says obituaries first; review argues foreclosure
  first ([[design-review-life-event-engine]], [[mecklenburg-foreclosure-slice]])
- Statewide vs Tier-1 collection
- Buy vs build parcel enrichment (Regrid / ATTOM vs 100 county GIS sites)
- Legal posture on scraping and on outreach
  ([[distressed-property-outreach-compliance]])
