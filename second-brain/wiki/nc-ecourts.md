---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, courts, north-carolina]
---

# NC eCourts (Odyssey) / Portal

North Carolina's statewide court case management system (Tyler Odyssey),
with a public web Portal for case search. The centralization argument for
statewide collection in [[life-event-property-intelligence-engine]] rests
entirely on this system.

## Why it matters to [[monitorclt]]

Single system of record for the case types that matter most: estate/probate
administration before the Clerk of Superior Court, foreclosure special
proceedings, tax foreclosure, partition, and civil judgments. One integration
instead of 100 county systems — *if* access is permitted.

## Status — verify before designing around it

- **Rollout:** the proposal claims all 100 counties as of **2025-10-13**.
  Consistent with a phased rollout completing in 2025, but **unverified
  here**.
- **Access terms:** public portals of this kind generally prohibit automated
  or bulk extraction and offer a separate paid bulk-data channel. **This is
  the highest-priority thing to check in the entire project** — see
  [[distressed-property-outreach-compliance]]. If scraping is out, the
  probate and foreclosure branches of the design need a licensing plan or
  must fall back to [[nc-press-association-public-notices]] and county
  sources.

## Practical notes

- Portal search is typically name/case-number oriented, not "everything
  filed today" — a poor fit for change detection even where permitted.
- Case-type coverage and field availability vary; confirm that estate and
  special-proceeding cases expose enough detail (parties, property
  references, filing dates) to be useful.
- Where a case is found, it is [[property-keyed-vs-person-keyed-sources]]
  person-keyed unless the filing names the real property.
