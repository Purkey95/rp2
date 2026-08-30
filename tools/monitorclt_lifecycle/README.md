# MonitorCLT Opportunity-Decay (lead lifecycle)

Every other module in MonitorCLT **detects** opportunity. This is the only one
that **retires** it.

Without a lifecycle, a parcel that scored 80 on a tax delinquency that's since
been paid — or one that sold last month — sits at 80 forever, and the lead list
slowly fills with ghosts. A lead is not a fact; it's a lifecycle: it strengthens,
weakens, resolves, or expires.

## Three mechanisms

- **Resolution** — a resolving event *after* the signal cancels it: tax paid, lien
  released, code case closed, occupancy restored. The `*` wildcard resolvers
  (`sold`, `deed_transfer`, `under_contract`) fire against *any* open distress on
  the parcel — a sale means the opportunity is gone or has moved to a new owner.
- **Decay** — a signal's weight halves every `half_life_days`, so a 2-week-old
  code case outweighs a 2-year-old one (`decay_factor` in (0, 1]).
- **Expiry** — a signal untouched for `expire_after_days` is stale and drops out.

Resolution and expiry take a signal out of `active`; decay just scales it.

## Run

```bash
python3 lifecycle.py --signals signals.jsonl --today 2026-08-19
python3 test_lifecycle.py
```

`apply()` is a **pre-filter for the score**: it returns only the still-live
signals, each annotated with lifecycle `state` and `decay_factor`, and separately
the retired ones so a run can report what fell off. Drop it into the pipeline
right before scoring and the existing five-score naturally stops counting resolved
and expired leads — no score change required. `decayed_points()` is provided for
when we want the score to also *weaken* aging leads, not just drop dead ones.

It's the mirror image of the [Catalyst Calendar](../monitorclt_catalyst/):
**decay retires stale leads, the calendar surfaces ripening ones** — same
dated-event machinery, pointed in opposite directions.

## Refinements (next)

- Feed resolution events from the sources that emit them (ROD lien releases, tax
  "paid" status, code-case "closed", new deeds) — several are already in reach.
- Let the score opt into `decayed_points()` so a lead visibly *weakens* over time
  instead of only dropping at the expiry cliff.
- Persist state transitions (active→resolved→expired) so we can report *lead
  velocity*: how fast the pipeline is turning over.
