---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, real-estate, charlotte]
---

# MonitorCLT

A Charlotte-area (Mecklenburg County, NC) real-estate intelligence system.

## Current state

The parcel enrichment layer is **built and loading live county data**
(`monitorclt/` in this repository, 2026-08-29): the full 428,504-parcel
Mecklenburg index loads in ~4 minutes with normalization, provenance and
person-to-parcel candidate generation. See
[[mecklenburg-cama-parcel-data]] for what the live data turned out to contain.

Everything else on this page is still design intent. Note that the `rp2`
repository hosting this work is an unrelated crypto tax calculator; the
`monitorclt/` package is self-contained and dependency-free precisely so it can
be lifted into its own repository without untangling.

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

- ~~Buy vs build parcel enrichment~~ — **decided 2026-08-29: build.** The
  Mecklenburg ArcGIS service is rich enough and fast enough that a vendor buys
  nothing for this county. The decision should be revisited for Tier 1
  expansion, where the cost is per-county heterogeneity rather than access.
- Build order — proposal says obituaries first; review argues foreclosure
  first ([[design-review-life-event-engine]], [[mecklenburg-foreclosure-slice]])
- Statewide vs Tier-1 collection
- Legal posture on scraping and on outreach
  ([[distressed-property-outreach-compliance]]) — **the open blocker**, and now
  the only thing between the parcel index and a scheduled pipeline
