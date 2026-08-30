---
type: answer
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, backtest, evaluation, negative-result, mecklenburg]
---

# Backtest results, August 2026

First measurement of [[monitorclt]] signals against real outcomes, using
1,492,220 Mecklenburg sales (1980–2026) reconstructed as of four historical
dates. Harness in `monitorclt/monitorclt/backtest/`.

**Headline: the tenure-based premise is backwards, and the estate premise
cannot be tested with the data available today.**

## Method

Owner state is reconstructed *from the sales chain only* — as of date T, the
owner is the grantee of the last sale on or before T, and tenure is T minus
that date. Scoring from current CAMA fields would leak the future, because
CAMA's owner and last-sale-date already reflect the sale being predicted. See
[[mecklenburg-cama-parcel-data]] for the underlying source.

Population is the ~395k properties with a reconstructable prior sale; ~33k
indexed parcels had none and are reported as uncovered rather than assumed
unsold.

## Finding 1 — long tenure anti-predicts a sale, robustly

Lift on a qualified arms-length sale within two years. **1.00x = no
predictive value.**

| Signal | 2012 | 2016 | 2019 | 2022 |
|---|---|---|---|---|
| `tenure_20y` | 0.45x | 0.44x | 0.48x | 0.49x |
| `tenure_30y` | 0.42x | 0.37x | 0.41x | 0.45x |
| `individual_tenure_30y` | 0.45x | 0.40x | 0.44x | 0.49x |
| `recently_bought_under_3y` *(control)* | 1.52x | 1.28x | 1.37x | 1.46x |
| `company_owner` | 2.01x | 1.43x | 1.52x | 1.73x |

Long-held property sells at roughly **half** the base rate, stably across a
decade and four independent windows. Obvious in hindsight — people who have
held for 30 years are the least likely to move — but it is the exact opposite
of the assumption in [[life-event-property-intelligence-engine]], whose
scoring ladder treated "owned 34 years" as a positive signal.

**The control outperforms every tenure signal.** A naive "predict a sale"
model finds churn: recent buyers and investors trading. That is the failure
mode [[property-signal-scoring-and-calibration]] warned about, now measured.

## Finding 2 — the estate outcome label does not exist in usable form

The `grantor` field records only **13–33 estate sales per year** countywide
(1,433 across 46 years), and the series is unstable: 129 in 2019, 13 in 2023.
Adding EXECUTOR / ADMINISTRATOR / PR variants contributes about 37 more in
total.

In a county of ~1.1M people, real estate-driven transactions must number in
the hundreds to low thousands annually. Estates typically sell under the
heir's or executor's *personal* name once title has passed, so the grantor
reads as an ordinary individual. The field captures the rare cases where
someone typed "ESTATE OF" onto a deed.

Consequence: **the estate hypothesis cannot be backtested against this data at
all.** The 3,468x lift measured for `decedent_marked_owner` at 2022 is
arithmetically correct against a label that captures perhaps 1% of real
estate transitions, and should not be quoted as validation of anything.

## Finding 3 — the forced-sale result does not replicate

An earlier read of the 2022 window alone suggested long tenure predicts
forced sale at ~2.5x. Across dates it does not hold:

| Signal | 2012 | 2016 | 2019 | 2022 |
|---|---|---|---|---|
| `tenure_20y` | 0.47x | 1.10x | 0.97x | 2.51x |
| `tenure_30y` | 0.26x | 1.47x | 0.84x | 2.60x |

The base rate itself moves 15-fold (0.44% in 2012 to 0.03% in 2022) as
post-crisis foreclosure volume collapsed, and hit counts are in the tens.
This is noise. **Recorded as a correction**: a single as-of date is not a
result, and the harness now says so in its own output.

## What this does and does not kill

**Killed:** tenure and "length of ownership" as scoring inputs, in the
direction assumed. Also any score built from the 20→97 ladder, which weighted
them positively.

**Not killed:** the distress premise itself. Every signal that would actually
carry it — foreclosure filings, tax delinquency, probate, code violations —
is *not in this data*. The backtest could not test them because they have not
been ingested.

**Still untestable historically:** absentee ownership, the largest cheap
signal at 167k parcels. Mailing addresses exist only in the current snapshot.
It can only be validated forward, starting from the snapshot taken
2026-08-29 — which is what `observed_at` is for
([[event-lifecycle-state-machine]]).

## What this implies for sequencing

It strengthens the case for [[mecklenburg-foreclosure-slice]] rather than
weakening it. The foreclosure ingest now has a second justification beyond
being property-keyed: it supplies both a real predictor *and* an outcome
label with enough volume to measure (10,126 forced sales are recorded
countywide, versus 1,433 estate-grantor sales).

The obituary and probate path in [[life-event-property-intelligence-engine]]
is not disproven — but nothing in the county's own records can currently
confirm it either, so building it first would mean building blind. That is a
stronger version of the argument in [[design-review-life-event-engine]] than
the one made there on structural grounds alone.
