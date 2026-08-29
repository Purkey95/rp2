---
type: concept
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, architecture, data-model]
---

# Event lifecycle state machine

[[life-event-property-intelligence-engine]] proposes tracking each subject
through a distress/transition lifecycle rather than treating events as
isolated. The idea is sound and generalizes across event types
(foreclosure, tax foreclosure, estate, bankruptcy). Three gaps in the
proposed version:

## Gap 1 — no event identity, so duplicates inflate

The same foreclosure sale surfaces as a court filing, a newspaper legal
notice, and an aggregator listing. Without a canonical event key, one real
event stacks three state transitions and three score increments.

Define an event identity key per event type — e.g. foreclosure:
`(county, case number)`; tax foreclosure: `(county, parcel id, sale date)`;
estate: `(county, estate file number)` — and merge on it. Sources become
*observations* of an event, not events. Keep all observations: multiple
independent sightings raise confidence in the *observation*, but must not
advance the lifecycle twice.

## Gap 2 — no exit states, so leads become zombies

The proposed chain ends at `TRANSFER / LISTING / SALE` with nothing that
removes a subject from the pipeline. Needed:

- **Terminal states:** sold, listed with an agent, estate closed,
  foreclosure cancelled/withdrawn, taxes brought current, bankruptcy
  discharged or dismissed.
- **Decay:** states expire. An estate opened 3 years ago with no further
  activity is not a live signal. Age of the most recent event should reduce
  rank, not sit at its peak forever.
- **Reactivation:** a terminal subject can re-enter on a new event.

## Gap 3 — states are asserted, not observed

Distinguish "we observed the estate was opened" from "we infer probate is
likely." Store an observed state plus its evidence, and derive inferences at
query time. Otherwise inference and fact become indistinguishable a month
later.

## Bitemporality

Store both `event_date` (when it happened) and `observed_at` (when we
learned it). Required for backtesting — see
[[property-signal-scoring-and-calibration]] — and for answering "what did we
know, and when?" Also snapshot the raw source document for audit.
