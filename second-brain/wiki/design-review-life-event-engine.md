---
type: answer
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, architecture, review, real-estate]
---

# Design review: Life Event & Property Intelligence Engine

Assessment of [[life-event-property-intelligence-engine]], 2026-08-29.

## What the proposal gets right

**Replacing sentiment with event extraction is the whole argument, and it is
correct.** Compressing a structured legal event into a scalar discards
exactly the fields that make it actionable. Everything downstream follows
from that fix.

Also right:

- **The foreclosure lifecycle model** (distress -> SP case -> notice of
  hearing -> authorization -> notice of sale -> auction -> upset bid -> final
  sale -> trustee deed). Tracking a parcel through the lifecycle genuinely
  beats buying a snapshot list, and the same shape generalizes across event
  types. See [[event-lifecycle-state-machine]].
- **Public notices as a first-class source.** Probably the best
  value-per-effort source in the whole matrix, and the one closest to
  "published for public consumption" — see
  [[nc-press-association-public-notices]].
- **Convergence on the parcel.** One owner/parcel record accumulating
  evidence from many event streams is the right data model.

## Objection 1 — it builds the hardest component first

The proposal leads with obituary -> probate, which is the entire
person-keyed matching problem, and then hand-waves the hard part as
`Owner Match Confidence: 96%`. That number carries enormous unearned weight.

Sort sources by one question: **does the record already name a parcel or
address?** ([[property-keyed-vs-person-keyed-sources]])

Real conditions for person-keyed matching: tax rolls store owners as
`SMITH JOHN A & MARY B`, or a trust, or an LLC, or a life estate;
obituaries often give no address at all; Mecklenburg has many John Smiths.
Precision dominates recall here, because a false positive means contacting
the wrong grieving family — the failure mode that kills businesses of this
type. Detail in [[entity-resolution-for-property-records]].

**Recommendation:** build Mecklenburg foreclosure + tax foreclosure first.
Property-keyed, so entity resolution is nearly free; and it yields the
parcel/owner index that person-keyed matching later needs to match
*against*. Obituaries are v2. Plan: [[mecklenburg-foreclosure-slice]].

## Objection 2 — invented precision

The scoring ladder (20 / 40 / 60 / 70 / 78 / 84 / 90 / 94 / 97) is authored,
not calibrated. `Estimated Equity: $384,000` sits next to
`Mortgage: Possible / none detected` — but absence of a recorded deed of
trust *in your scrape* is not absence of a mortgage, and assessed value is
not market value.

Ship a ranked list with an evidence trail before shipping a number, and
assign numbers only once they can be calibrated against outcomes. That
requires a backtest and bitemporal storage from day one — see
[[property-signal-scoring-and-calibration]].

## Objection 3 — compliance determines build order, not just risk posture

Several sources the proposal treats as available may be closed by terms of
use or licensing, which changes what can be built at all. NC eCourts portal
terms, Legacy/Echovita scraping prohibitions, and the outreach rules around
distressed and pre-foreclosure owners are the ones to check before
designing around them. Full list:
[[distressed-property-outreach-compliance]].

## Objection 4 — statewide collection is premature

Collection is not the cost; **enrichment** is. Parcel and tax data is
per-county and heterogeneous across all 100 NC counties. Statewide coverage
is where a vendor (Regrid, ATTOM) beats DIY. Keep the Tier 1 footprint as
proposed; drop "collect NC statewide" until the enrichment path is settled.

## Objection 5 — the source matrix is the wrong next step

A 100-county x 12-source matrix is weeks of research that produces no
running system and goes stale as county sites change. Scope it to
Mecklenburg + the two foreclosure types, build the slice, then let the
matrix grow from something that runs.
See [[public-record-source-matrix-mecklenburg]].

## Smaller gaps

- **No deduplication.** The same foreclosure arrives via court filing,
  newspaper notice, and an aggregator. Without a canonical event identity
  key, the state machine stacks one event into three score bumps.
- **No exit states.** The state machine needs terminal states (sold, listed
  with an agent, estate closed) and decay, or the pipeline accumulates
  zombie leads forever.
- **No audit trail.** Snapshot raw source HTML. When a match is wrong you
  need to be able to reconstruct why.

## Outcome (2026-08-29)

The backtest was built and run — [[backtest-results-2026-08]]. Objection 2
turned out to understate the problem: the ladder was not merely uncalibrated,
its tenure terms had the wrong sign. Objection 1's build order is reinforced,
now on measured rather than structural grounds.

## Verdict

Adopt the architecture. Invert the build order. Do not ship a score until
it is backtested.
