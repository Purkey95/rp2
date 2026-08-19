# MonitorCLT Valuation (ARV/AVM + CMA)

Everything else in MonitorCLT answers *who to buy from and why now*. Nothing said
*what it's worth* — and without that you can't make an offer. **This is that half.**
It's the acquisition-side answer to the MLS CMA tools (e.g. ListingBeast /
"CMAs With Claude"): same engine, pointed at the offer instead of the listing.

## Two tiers, one engine

| Tier | Comp pool | Access | Use |
|---|---|---|---|
| **Records AVM** (default) | recorded deed sales + assessor characteristics | free/records (data we hold) | the number that sets your **max offer** on an off-market lead |
| **Retail CMA** | MLS sold comps + photos/condition | mls-paid | listing-grade report for dispo |

Only the comp pool changes; the code is identical. MLS becomes a drop-in data
upgrade, same pattern as every other gated source in the taxonomy.

## Method (a transparent, per-comp appraisal grid)

1. **Select comps** — same property type, nearby (haversine miles), recent, within
   sqft / lot / age tolerances.
2. **Adjust each comp *to* the subject** — time (market drift since sale) plus
   $/sqft, beds, baths, lot, age deltas. Every dollar is in the grid.
3. **Reconcile** — weight by recency + distance + *how little* adjustment each
   needed (the least-adjusted comp is the best comp); take the weighted mean.
4. **Offer band** — as-is wholesale band (`estimate × 0.65–0.80`) plus the
   investor **70%-rule MAO** (`ARV × 0.70 − repairs`) when repairs are supplied.

## Run

```bash
python3 valuation.py --subject subj.json --comps comps.jsonl \
  --today 2026-08-19 --repairs 40000
python3 test_valuation.py
```

Output: estimated value, value range, a confidence score (tighter comp cluster +
more + fresher comps = higher), the as-is offer band, the 70%-rule MAO, and the
full adjustment grid. Reports `insufficient_comps` rather than guessing when the
pool is too thin. Pass `--today` for the time adjustment (determinism).

## How it plugs in

`score` → *pursue this owner* · `valuation` → *it's worth ~$X, so offer $Y*. The
Seller Opportunity Score ranks the lead; the valuation sizes the offer. Together
they turn a signal into a deal.

## Calibrate before trusting the dollars

The adjustment constants in `valuation_rules.json` (`per_sqft`, `per_bath`,
`monthly_appreciation`, …) are Charlotte-area SFR placeholders. Tune them per
submarket against known sales before relying on the output for real offers — the
*method* is sound; the *coefficients* are yours to set.
