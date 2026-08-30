---
type: concept
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, scoring, calibration, evaluation]
---

# Property signal scoring and calibration

## The sentiment critique is correct

Scoring an obituary as `sentiment = -0.94` is a category error: a
structured legal event is compressed into a scalar that discards name, date,
county, parcel, and every field that made it actionable. Replacing it with
event extraction is the central and correct move of
[[life-event-property-intelligence-engine]].

## But the replacement repeats the mistake

The proposed ladder —

```
obituary only 20 / + property match 40 / estate opened 60 /
executor identified 70 / high equity 78 / out-of-area executor 84 /
vacant 90 / tax delinquent 94 / code violations 97
```

— is authored, not calibrated. The numbers imply an accuracy that nothing in
the system has established. `Opportunity Score: 94/100` and
`sentiment = -0.94` fail the same way: both are precise-looking scalars with
no measured relationship to any outcome.

Related: `Estimated Equity: $384,000` beside
`Mortgage: Possible / none detected`. Absence of a recorded deed of trust
*in the scrape* is not absence of a mortgage, and assessed value is not
market value. Report the inputs and their provenance, not a derived dollar
figure that launders both uncertainties.

## What to ship instead

1. **A ranked list with an evidence trail.** "Estate opened; no mortgage
   found in recorded index; owner 34 yrs; executor address out-of-state" is
   more useful to an operator than `94`, and it is falsifiable.
2. **Numbers only after calibration.** A score of 80 should mean the
   observed outcome rate at that score is ~80%. Until that has been
   measured, the number is decoration.

## Backtest before scoring

Take ~100 known Mecklenburg estate/distress sales from 2024-25. Replay the
pipeline against data **as of then** — which requires the `observed_at`
field from [[event-lifecycle-state-machine]]. Measure:

- **Coverage:** what fraction would have been flagged at all?
- **Lead time:** how many days before the transaction?
- **Precision:** of everything flagged in that window, how much transacted?
- **Rank quality:** did the top decile outperform the rest?

Without this, the score is vibes with a decimal point.

## Baselines to beat

Any score must beat trivial alternatives on the same backtest, or it is not
earning its complexity:

- Every foreclosure notice, unranked
- Every tax-delinquent parcel, sorted by amount owed
- Owner age proxy (length of ownership) alone
- A purchased list from an incumbent data vendor

## Result

Run 2026-08-29. See [[backtest-results-2026-08]]. The backtest was built and
the ladder did not survive it: long tenure anti-predicts sales at ~0.45x lift,
stable across four windows, and the control signal (recent buyers) outperforms
every tenure signal — the exact "measuring churn" failure listed below. The
estate outcome label turned out to be unusable, so that hypothesis remains
untested rather than confirmed or denied.

## Watch for

- **Feature leakage** — a "signal" that is really a consequence of the sale.
- **Collinearity** — tax delinquency, code violations and vacancy co-occur;
  stacking them triple-counts one underlying condition.
- **Survivorship** — backtesting only on properties that *did* transact
  measures nothing about false positives. Sample the non-transacting
  population too.
