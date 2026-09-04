# Changelog

## 2.1.0 — 2026-09-04

- Live Mecklenburg sources: assessor roll, code enforcement, liens, vacant land,
  address points (City of Charlotte ArcGIS) and the Register of Deeds index
  (Aumentum ROD Web Access). County registry profiles: `sample` (synthetic) and
  `live`; `--profile` is now required on `ingest`, `contract` and `run-daily`.
- Matching: trust-string parsing, nickname / initial / surname-phonetic blocking,
  weak-name gate, best party per record, `sale_after_death`, geocoded
  `coordinates_match`, `care_of` parties as related parties.
- Review: estate-centric groups, review-value ordering, audit sampling, double
  review and agreement report. Outcomes loop with suppression and cadence.
  Person clustering. Business-registry enrichment. Daily pipeline with alerting.
- Security: the API requires a token or a trusted proxy identity header unless
  `--insecure-local` is passed; webhook secrets are environment references;
  live transports carry cookies and a politeness interval on the connector.

## 2.0.0 — 2026-09-04

- Rebuild from the v1 probate cross-reference: bitemporal history, connector
  framework, entity graph, calibrated resolver with hard gates, review loop,
  policy layer, watchlists, signal stack, data-quality monitoring, API and CLI.
