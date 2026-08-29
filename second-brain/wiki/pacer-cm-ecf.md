---
type: entity
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, bankruptcy, courts]
---

# PACER / CM-ECF (bankruptcy)

Federal court docket access. For [[monitorclt]] the relevant court is the
**U.S. Bankruptcy Court for the Western District of North Carolina**
(Charlotte division), per
[[life-event-property-intelligence-engine]] — unverified here.

## The RSS opportunity, qualified

CM/ECF installations can expose a public RSS feed of recent docket activity
(typically a rolling 24-hour window). This is real and legitimately usable,
but the proposal overstates it:

- The feed is **per-court** and must be enabled by that court.
- **Event-type coverage varies** — some courts publish only selected entry
  types, and new-case entries are not guaranteed to be among them.
- The feed carries **docket text, not case detail**; petitions and schedules
  are documents behind metered PACER retrieval.

**Verify what W.D.N.C. actually publishes before designing a bankruptcy
branch around it.**

## Value once resolved

Bankruptcy is **person-keyed**
([[property-keyed-vs-person-keyed-sources]]): the petition names a debtor,
and real property appears in schedules rather than the docket line. So it
inherits the full cost of
[[entity-resolution-for-property-records]] — another reason it belongs after
the property-keyed slice, not before it.

The upside the proposal identifies is genuine: a single debtor can resolve
to a multi-property portfolio, making one filing a portfolio-level signal.

## Related, and easier

Motions for relief from stay often **name the property** — a property-keyed
record inside a person-keyed source. Also: 341 meeting calendars, case
dispositions (discharge vs. dismissal — a dismissal often returns a property
to foreclosure), and trustee abandonment.
