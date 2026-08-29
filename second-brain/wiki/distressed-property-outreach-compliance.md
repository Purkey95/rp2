---
type: concept
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, legal, compliance, risk]
---

# Distressed property: data and outreach compliance

Constraints that determine **what [[monitorclt]] can be built from**, not
just how risky it is. Several sources treated as available in
[[life-event-property-intelligence-engine]] may be closed by terms of use,
which changes the architecture rather than the disclaimer.

> Everything below is a **checklist of things to verify**, several of them
> with counsel. Nothing here is a legal conclusion, and no statute is cited
> from memory — look each one up before relying on it.

## Data acquisition

| Source | Concern | Effect if true |
|---|---|---|
| [[nc-ecourts]] public portal | Portal terms typically prohibit automated/bulk extraction; a separate paid bulk-data channel usually exists | "Monitor eCourts" becomes a licensing line item, not a scraper. **Check first — much of the proposal rests on it.** |
| [[pacer-cm-ecf]] | RSS is legitimate and free; document retrieval is metered; PACER has its own terms | Fine, but narrower than the proposal implies |
| Legacy.com / Echovita | ToS prohibit scraping; Legacy in particular enforces | Rules out the two aggregators named in the proposal |
| Individual funeral home sites | Generally scrapeable, but ~200 fragile parsers to own forever | Maintenance cost likely exceeds a licensed death feed |
| SSA Death Master File | The full file is access-restricted; the public version is limited and certification-gated | Not a free shortcut |
| [[nc-press-association-public-notices]] | Published expressly for public notice | Best value-per-effort; least friction |
| County tax / GIS / register of deeds | Usually genuinely open, sometimes with bulk-download products | Build here freely; check per-county terms |

## Outreach

Contacting people identified through these signals is separately regulated
from collecting the data. Verify with counsel before the first campaign:

- **TCPA and Do-Not-Call** for calls and texts; wrong-number and reassigned
  number exposure is real and statutory damages are per-contact.
- **NC foreclosure-rescue and homeowner-protection statutes.** North
  Carolina regulates solicitation of homeowners in or near foreclosure,
  including required disclosures and prohibited practices. Look up the
  current text; do not rely on summaries.
- **Unsolicited-offer and wholesaling rules**, including NC real estate
  licensing boundaries around marketing a property you do not own.
- **FCRA-adjacent risk.** Direct marketing of your own purchase offer is
  generally outside FCRA. **Selling scores to third parties** as an input to
  eligibility decisions moves toward consumer-report territory. Get this
  reviewed before any B2B data product.
- **FTC/CFPB attention** on distressed-homeowner marketing generally.

## Product rules worth adopting regardless

Cheap insurance against the story that ends the business:

- **A suppression window on death events.** No outreach for a defined period
  after a date of death. Treat as a product rule, not a nicety.
- **Suppression list**, honored across every channel, permanently.
- **A high promotion bar for person matches**
  ([[entity-resolution-for-property-records]]) — a false positive here means
  contacting the wrong family about a death.
- **Provenance on every record**, so any claim can be traced to a source
  document and a date, and a complaint can be answered with facts.
