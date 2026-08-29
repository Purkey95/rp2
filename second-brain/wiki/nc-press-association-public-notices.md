---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, legal-notices, north-carolina]
---

# NC Press Association public notices

Statewide searchable database of legal notices published in North Carolina
newspapers. Likely the **best value-per-effort source** in
[[public-record-source-matrix-mecklenburg]].

## Why it is the strongest starting source

- **Published expressly for public notice** — the least friction of any
  source in [[distressed-property-outreach-compliance]].
- **Statewide and centralized**, unlike county tax and GIS sites.
- **Structurally predictable text.** Notices follow statutory templates, so
  extraction is regex-plus-parser work, not open-domain NLP.
- Carries both estate and foreclosure notices — covering
  person-keyed and property-keyed events from one integration.

## Notice types of interest

**Estate:** notice to creditors, "having qualified as Executor /
Administrator", letters testamentary, letters of administration, personal
representative, notice to heirs and devisees.

**Foreclosure:** notice of foreclosure sale, substitute trustee, power of
sale, notice of hearing, upset bid, in-rem tax foreclosure.

**Other:** partition/special proceedings, condemnation, service by
publication, guardianship.

## Extraction targets

Case number (the join key to [[nc-ecourts]] and the event identity key in
[[event-lifecycle-state-machine]]); decedent or borrower name; property
address or parcel where stated; trustee/attorney; executor/administrator and
their address (out-of-area executor is a signal); sale date, hearing date,
upset bid deadline; publication and first-run dates.

## Caveats

- Publication **lags** the filing; the court record is earlier where
  accessible.
- Address is present in foreclosure notices far more reliably than in
  estate notices — which is exactly the
  [[property-keyed-vs-person-keyed-sources]] split.
- Verify search/export terms and whether any API or bulk access exists
  before building a scraper.
