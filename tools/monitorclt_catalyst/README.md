# MonitorCLT Catalyst Calendar

The third temporal axis. The score says **what** is wrong now; the timeline
engine says **why now**; this says **what's about to happen**.

Almost every record we ingest carries a date, and many of those dates point at a
*future* milestone that will move the property — a private loan maturing, a
rezoning hearing, tax-foreclosure eligibility, an entitlement or ground lease
expiring, a special assessment taking effect. The calendar projects those dates
forward per parcel so we can answer the operationally useful question:

> **Which 37 parcels have a meaningful catalyst in the next 60 days?**

That's the query acquisitions actually runs on a Monday morning.

## How a catalyst enters

- **Explicit** — a record already carries a real future date (a rezoning hearing
  date, a recorded ground-lease expiration). Passed straight through.
- **Derived** — a present-dated signal implies a milestone at a known offset
  (`tax_delinquency` today → `tax_foreclosure_eligibility` ~2 years out). Offsets
  live in `catalyst_rules.json`, are approximations, and each derived catalyst is
  flagged `derived: true` and carries the rule's `note`. Explicit dates always win.

Each upcoming catalyst is weighted by **type** (a foreclosure sale date outweighs
an infrastructure completion) and by **imminence** (a maturity 20 days out
outweighs one 300 days out). Per parcel we emit the sorted upcoming catalysts,
`days_until` each, and a `catalyst_horizon` score (0–100).

## Run

```bash
python3 catalyst.py --events events.jsonl --today 2026-08-19 --within 60
python3 test_catalyst.py
```

Input rows are JSONL — either explicit (`apn, catalyst_type, date, source_url`) or
plain signals (`apn, signal_type, date, source_url`) that the derived rules
project forward. Only catalysts dated today-or-later and inside the horizon are
returned; past events belong to the timeline/lifecycle engines.

Outputs `catalysts.jsonl` (per-parcel calendar) and `catalyst_signals.jsonl` — a
`catalyst_horizon` signal per parcel that the five-score consumes like any other
(disposition dimension). It's the mirror image of the lifecycle engine: **decay
retires stale leads, the calendar surfaces ripening ones** — same dated-event
machinery, both directions.

## Refinements (next)

- Pull real recorded dates as sources come online: ROD deed-of-trust maturity
  dates (hard-money), Charlotte rezoning hearing dates, recorded ground-lease and
  entitlement-condition expirations.
- Per-catalyst reliability: an explicit recorded date is hard; a derived offset is
  a guess — feed both into the confidence model.
