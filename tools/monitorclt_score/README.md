# MonitorCLT Owner Distress Score

Turns the wired signals into **ranked, traceable acquisition leads**. This is the
synthesis layer: it joins the sourcing system's `signals` to the parcel/owner
layer, computes the derived and behavioral signals we can compute from owned
data, stacks them, and emits a 0-100 score per parcel where every point traces
back to its source.

It's the "stack them" thesis as code: one signal rarely means much; a signal
from two or three *categories* (financial + deterioration + ownership +
behavioral) is where the probability of a real problem jumps.

## Run it

```bash
python3 score.py --signals sample/signals.jsonl --parcels sample/parcels.csv \
    --year 2026 --outdir out
```

Outputs: `scored_leads.jsonl` (full detail + evidence), `scored_leads.csv`
(ranked flat table), `summary.json` (band counts + metrics). Test:
`python3 test_score.py`.

## How it scores

1. **Sourced signals** (from the adapters' `signals.jsonl`) — only **active**-bucket
   signals count; a `resolved` signal means the problem cleared, so it's excluded.
   Each contributes its weight and keeps its `source_url` as evidence.
2. **Derived signals** (computed from the parcel/owner, no new source):
   `absentee` (mailing ≠ situs), `out_of_state`, `long_tenure_20y`,
   `high_equity_proxy` (only when a mortgage figure is present — parcel value alone
   can't prove equity, so it's never guessed), `large_portfolio`,
   `recently_sold_another` (the behavioral edge — owner disposed of a parcel while
   holding others; from entity resolution over owner name).
3. **Stacking bonus** — `+6` per extra category beyond the first (capped `+18`),
   because cross-category signals compound.
4. **Bands** — 81-100 immediate · 61-80 priority · 41-60 mail+call ·
   21-40 digital · 0-20 skip.

Weights, categories, and bands all live in `weights.json` — tune freely.

## What actually feeds it today vs. what's config-ready

The score is honest: it scores what's present. From the 17 live sources you can
feed it **now**: tax delinquency, tax sale, foreclosure, code violations /
nuisance / housing, demolition, vacancy, unpermitted work, environmental, plus
the derived absentee / out-of-state / tenure / portfolio / behavioral signals.

`weights.json` also carries **config-ready** weights for signals not yet wired to
a source — HOA/municipal/judgment/mechanic's liens, probate/inherited, divorce,
bankruptcy, eviction/repeat-eviction, failed inspection, expired MLS, utility
inactive, fire/storm damage. They score automatically the moment a source emits
that `signal_type`, so wiring Register-of-Deeds liens or court records later is a
data task, not a scoring change. (MLS, utilities, and insurance need paid/licensed
data — see the roadmap.)

## Data contract

- **signals**: JSONL from the sourcing adapters — needs `signal_type`, `bucket`,
  `apn`, `source_url`.
- **parcels**: CSV/rows from the enrichment parcel table — `apn`, `owner`,
  `situs_street`, `mail_street`/`mail_state`, `property_state`, `sale_year`,
  `assessed_value`, `mortgage_balance` (optional), `vacant`.

## Wiring to the live database

Replace `load_signals`/`load_parcels` with Postgres queries (`signals` table +
the enrichment `parcels` view), write `scored_leads` back to a `leads` table, and
register `score.leads_priority_plus` (and band counts) as MonitorCLT metrics so
the daily digest shows how many priority+ leads exist. The score is recomputed
whenever new signals land — a lead's score rises as its problems stack.
