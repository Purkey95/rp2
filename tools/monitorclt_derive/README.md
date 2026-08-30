# MonitorCLT Derived-History Signals

The cheapest, highest-leverage builds in the taxonomy: **no new source**, just
reading the *history* of records we already pull and deriving a signal worth far
more than any single row.

## Signals

- **`tax_delinquency_multiyear`** — one delinquent year is a signal; *N consecutive
  years with a rising balance* is a story, and in NC ~2 years of delinquency is
  foreclosure-eligible. Timing arbitrage: the trajectory, not the snapshot. Emits
  when a parcel is delinquent ≥ `min_consecutive_years` in a row, flags rising
  balance and `[foreclosure-ripe]` at `hot_consecutive_years`.
- **`complaint_velocity_311`** — one 311 complaint is noise; a *rising* count in a
  trailing window (5 this year, up from 1) is a property visibly deteriorating
  before a formal code case opens. Fires only when recent ≥ `min_recent` **and**
  rising vs the prior equal window.
- **`stalled_subdivision`** — a recorded plat with effectively no vertical (building)
  permits since = a subdivision that stalled after paper approval. Negative-space:
  the expected follow-up (homes going up) never happened.

## Run

```bash
python3 derive.py --today 2026-08-19 \
  --tax-history tax_history.jsonl \
  --complaints complaints_311.jsonl \
  --plats plats.jsonl --permits permits.jsonl
python3 test_derive.py
```

Every derivation emits a parcel-keyed signal the five-score consumes like any
other, with evidence describing the derivation (e.g. "3 consecutive delinquent
years (2024-2026), balance $1,200 → $4,100 (rising) [foreclosure-ripe]").

Inputs are whatever we already ingest, re-shaped as history: tax rows per year,
311 complaints with dates, recorded plats + permits. Determinism via `--today`.
