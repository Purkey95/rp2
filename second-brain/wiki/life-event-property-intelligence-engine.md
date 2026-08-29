---
type: source
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, real-estate, public-records, architecture, proposal]
---

# Life Event & Property Intelligence Engine (proposal)

Source: design proposal for [[monitorclt]], received in conversation
2026-08-29 (not filed in `raw/` — pasted directly). This page records the
proposal **as received**; the assessment lives in
[[design-review-life-event-engine]].

## Core thesis

Replace the existing `news -> sentiment analysis` pipeline with an event
extraction engine over public records. A death notice scored as
`sentiment = -0.94` destroys the information that made it useful; the same
notice parsed as a structured event (name, date, age, county, likely parcel)
is actionable. See [[property-signal-scoring-and-calibration]] for why the
scalar is the wrong primitive.

## Proposed architecture

```
NEWS        LEGAL EVENTS        PROPERTY DATA
 RSS        Courts / Probate    Parcels
 Media      Bankruptcy          Tax
            Foreclosure         Deeds
            Public notices
              |
        ENTITY ENGINE  (person / LLC / address -> parcel)
              |
        SIGNAL ENGINE  -> opportunity score
```

The entity engine is the load-bearing component — see
[[entity-resolution-for-property-records]].

## Event taxonomy proposed

Death/estate (obituary, notice to creditors, letters testamentary,
executor/administrator qualification, intestate/testate, heirs, devisees,
estate sale); foreclosure/distress (notice of hearing, substitute trustee,
power of sale, upset bid, in-rem tax foreclosure, HOA/condo lien, delinquent
taxes); bankruptcy (Ch. 7/11/12/13, 341 meeting, automatic stay, relief from
stay, abandonment); transition (partition, heirs property, quiet title,
condemnation, eminent domain, lis pendens, judgment, mechanic's lien,
receivership, guardianship, divorce). Grouped and re-prioritized in
[[property-keyed-vs-person-keyed-sources]].

## Proposed state machine

`DEATH DETECTED -> POSSIBLE OWNER MATCH -> OWNER MATCHED -> PROBATE WATCH ->
ESTATE OPENED -> EXECUTOR IDENTIFIED -> NOTICE TO CREDITORS -> PROPERTY
STATUS -> TRANSFER / LISTING / SALE`, with an accumulating score
(obituary only 20 -> ... -> code violations 97). Critiqued in
[[event-lifecycle-state-machine]] and
[[property-signal-scoring-and-calibration]].

## Proposed geography

Collect NC statewide (justified by [[nc-ecourts]] centralization), enrich
Tier 1: Mecklenburg, Union, Cabarrus, Gaston, Iredell, Lincoln, Rowan,
Catawba, Burke. Tier 1B: York SC, Lancaster SC.

## Proposed next step

Build a complete "Life Event & Distress Source Matrix" — every source, every
county, with access mode (RSS/API/download/search/scrape) and cost.
Scoped down in [[public-record-source-matrix-mecklenburg]].

## Factual claims made by the source — UNVERIFIED

Date-stamped because recency matters; each needs confirmation before any
design depends on it.

| Claim | Status |
|---|---|
| All 100 NC counties on statewide eCourts/Odyssey as of 2025-10-13 | Plausible (phased rollout completed 2025); **verify** |
| Echovita shows 15,000+ Charlotte obituary records | Unverified; irrelevant if ToS forbids collection |
| NC wills become public record once filed; estates run through Clerk of Superior Court | Consistent with NC practice; verify statute |
| NC foreclosure begins with trustee's notice of hearing before the Clerk, creating a special proceeding; sale must be advertised + posted | Consistent with NC power-of-sale practice; verify |
| Mecklenburg County publishes tax-foreclosure listings, attorney assignments, auctions | Verify current URL/format |
| Charlotte is in Bankruptcy Court W.D.N.C. (Charlotte, Asheville, Statesville) | Consistent; verify |
| CM/ECF exposes a public RSS feed of last-24h docket activity | True in general, but per-court and per-event-type; **verify for W.D.N.C.** — see [[pacer-cm-ecf]] |

**Conflict watch:** the source treats each of these as settled. None has been
independently checked in this vault.
