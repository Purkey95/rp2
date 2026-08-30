---
type: answer
created: 2026-08-30
updated: 2026-08-30
tags: [monitorclt, propensity, scoring, evaluation, negative-result]
---

# Propensity scoring: it works, and it ranks the wrong people

Status of propensity scoring for [[monitorclt]], measured 2026-08-30.
Model in `monitorclt/monitorclt/propensity/`.

## Result

A cell model over `(owner_type, tenure_bucket)`, trained on 2016-2018 and
validated **out of time** on 2022-2024:

| Decile | n | Predicted | Actual | Lift | Cum. recall |
|---|---|---|---|---|---|
| 1 | 39,541 | 24.30% | **23.66%** | **2.53x** | 25% |
| 2 | 39,541 | 14.71% | 10.24% | 1.09x | 36% |
| 3 | 39,541 | 14.53% | 9.28% | 0.99x | 46% |
| 4 | 39,541 | 14.09% | 8.48% | 0.91x | 55% |
| 5 | 39,541 | 12.99% | 9.39% | 1.00x | 65% |
| 6 | 39,541 | 12.47% | 9.10% | 0.97x | 75% |
| 7 | 39,541 | 10.94% | 8.16% | 0.87x | 84% |
| 8 | 39,541 | 8.96% | 6.67% | 0.71x | 91% |
| 9 | 39,541 | 6.35% | 5.18% | 0.55x | 96% |
| 10 | 39,541 | 4.14% | 3.46% | 0.37x | 100% |

Base rate 9.36%. **The top decile is genuinely predictive** — 2.53x lift,
capturing a quarter of all arms-length sales in a tenth of the population.

## Three things the table says

**It only separates the top decile.** Deciles 2 through 7 all sit within a
few points of 1.00x. This is not a smooth ranking of 395,410 properties; it
is one useful cut and then noise. Any product treating the score as a
continuous rank below the top decile is selling precision it does not have.

**Absolute probabilities do not transfer.** Predicted 14.71% against actual
10.24% in decile 2, because the training window's base rate was 12.38% and
the test window's was 9.36% — the market cooled between them. Only the
*ranking* survives a regime change. Read lift, never the predicted
percentage.

**The top decile is investor inventory.** The highest-scoring cells:

| Cell | Train n | Predicted |
|---|---|---|
| COMPANY, 0-3 yrs | 27,299 | 24.4% |
| TRUST, 0-3 yrs | 658 | 18.1% |
| PERSON, 3-7 yrs | 46,525 | 14.7% |

The model learned *recently-transacted property transacts again*. Its top
decile is companies that bought within three years — investor stock being
flipped, plus recent buyers reselling.

## Why that makes it commercially useless as-is

Those owners are the **worst** possible leads for the business
[[life-event-property-intelligence-engine]] describes: sophisticated,
undistressed, unmotivated, already transacting through agents, and selling at
market. The properties that generate the opportunity — inherited, distressed,
tax-delinquent, deferred-maintenance — are in the *bottom* deciles, because
long-held owner-occupied property rarely sells in any given two years.

This is the same wall as [[backtest-results-2026-08]], reached from the
modelling side. Optimising a sale-propensity model against these features
optimises *away* from the target population. A better-fitted model would be a
worse product.

## What this means for the score

Do not ship this as a lead ranking. It is worth keeping for two other things:

1. **A baseline.** Any future distress-driven score must beat 2.53x top-decile
   lift, or it is adding complexity for nothing.
2. **An exclusion filter.** Its top decile is a reasonable *negative* list —
   property likely to trade on the open market without any intervention.

A propensity score becomes the right tool once the predictors are distress
events (foreclosure filings, tax delinquency, probate, code violations)
rather than churn proxies. The model code is agnostic to which features it is
handed; the features are the problem, not the method. See
[[mecklenburg-foreclosure-slice]].

## Method notes

Guarded in code because both mistakes produce results that look excellent:

- **Train and test windows must not overlap**; the CLI refuses overlapping
  windows rather than warning.
- **Ties must never be broken by the outcome.** A cell model has ~40 distinct
  scores over 395,410 rows, so most of the population sits in large tied
  blocks. An early run sorted `(score, outcome)`, ordering positives first
  inside every block — it manufactured a 2.56x top decile with impossible
  0.00% deciles in the middle. There is a regression test for it.
