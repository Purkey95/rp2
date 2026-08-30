---
type: index
created: 2026-07-08
updated: 2026-08-29
---

# Wiki Index

Catalog of every page in the wiki, one line each. Maintained by the LLM;
updated on every ingest. This is the primary retrieval mechanism — read it
first when answering queries.

## Sources

- [[life-event-property-intelligence-engine]] — proposed rebuild of MonitorCLT around public-record event extraction instead of news sentiment; includes a table of the source's unverified factual claims.

## Entities

- [[monitorclt]] — Charlotte-area real-estate intelligence system; design intent only, no code in this vault.
- [[nc-ecourts]] — NC statewide court system (Odyssey) and its public Portal; access terms are the project's highest-priority open question.
- [[nc-press-association-public-notices]] — statewide legal-notice database; likely the best value-per-effort source.
- [[pacer-cm-ecf]] — federal bankruptcy dockets and the per-court RSS feed; narrower than commonly assumed.
- [[mecklenburg-cama-parcel-data]] — the county ArcGIS parcel/owner service; verified against live data, with five measured traps and current signal counts.

## Concepts

- [[property-keyed-vs-person-keyed-sources]] — the sorting principle: does the record already name a parcel? Determines build order.
- [[entity-resolution-for-property-records]] — matching people to parcels; the load-bearing component, and why precision dominates recall.
- [[event-lifecycle-state-machine]] — tracking subjects through distress lifecycles; needs event identity, exit states, and bitemporality.
- [[property-signal-scoring-and-calibration]] — why an authored score repeats the sentiment mistake; backtest design and baselines.
- [[distressed-property-outreach-compliance]] — data-acquisition and outreach constraints that determine what can be built at all.

## Syntheses & Answers

- [[design-review-life-event-engine]] — assessment of the proposal: adopt the architecture, invert the build order, don't ship an uncalibrated score.
- [[mecklenburg-foreclosure-slice]] — the recommended v0: a property-keyed foreclosure vertical slice, with a definition of done.
- [[public-record-source-matrix-mecklenburg]] — the source matrix scoped from 100 counties down to one, ordered by build phase.
- [[backtest-results-2026-08]] — first measurement against real outcomes: long tenure anti-predicts sales, and the estate label is unusable.
- [[propensity-scoring-findings]] — a propensity model validated at 2.53x top-decile lift, whose top decile is investor inventory rather than motivated sellers.
