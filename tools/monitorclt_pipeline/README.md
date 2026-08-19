# MonitorCLT Pipeline

The whole engine as one command. Every other module does one job; this runs them
in order so raw signals come in and a **ranked offer sheet** comes out.

```
raw signals
   -> AUGMENT    derive-history + geometry + info + network + catalyst signals
   -> LIFECYCLE  drop resolved/expired leads (keep only live)
   -> SCORE      five-component Seller Opportunity Score + confidence
   -> WHY-NOW    temporal score from dated events (optional)
   -> VALUE      records AVM + offer band on each marketable lead
   -> OFFER SHEET  ranked: who to pursue, why, and what to offer
```

This file only orchestrates — every step is an existing, tested module.

## Run

```bash
python3 pipeline.py \
  --signals signals.jsonl --parcels parcels.csv --comps comps.jsonl \
  --tax-history tax_history.jsonl --complaints complaints.jsonl \
  --plats plats.jsonl --permits permits.jsonl --addresses addresses.jsonl \
  --catalyst-events catalysts.jsonl \
  --today 2026-08-19 --year 2026
python3 test_pipeline.py
```

Only `--signals`, `--parcels`, `--today`, `--year` are required; every history /
geometry / catalyst input is optional and simply adds more derived signals when
present. `--comps` enables valuation; `--value-threshold` sets the minimum score to
run one (default 21).

## What one row looks like

```
10 OAK ST     score 70 [priority] conf 88 why-now 46.0  est $306,184  offer $199,020-$244,947
    signals: tax_delinquency, vacancy, hidden_density_zoning_mismatch, tax_lot_legal_lot_mismatch, absentee
```

`score` ranked the lead; `valuation` sized the offer; the augment step folded in
the derived hidden-density and legal-lot signals with no extra input; lifecycle
dropped anything resolved. That's a signal turned into a deal, end to end.

## Testable core

`run()` takes already-loaded python objects and returns the offer-sheet rows plus
run stats (augment counts, live/dropped, lead count), so the flow is unit-tested
without touching the filesystem. `main()` is just files/CLI around it.

## Market overlay (when/where gate)

Pass `--market-offline DIR` (a folder of cached FRED `<id>.csv`) and the pipeline
builds the market posture and applies it as a **macro gate**:

- attaches `market_context` (stance + reasons) to the run and `market_stance` to
  every row;
- in a soft-exit market (≥2 exit cautions) adds a **conservative
  `market_adjusted_high`** to each offer band — the original band is untouched, and
  the parcel **score is never modulated** (the discipline: market moves the *offer*
  and the *read*, never the *score*).

Stances: `lean_in`, `caution`, `source_aggressively_underwrite_conservatively`,
`neutral`.

## Persistence

Pass `--dsn "postgresql://…"` to upsert the offer sheet into the `leads` table so
the CRM, daily digest, and mail/SMS lanes read one canonical **`offer_queue`** view
(priority leads that already carry a valuation + offer band). Apply
`../monitorclt_score/schema.sql` then this module's `schema.sql` first;
`offer_db.py` does the upsert (psycopg lazy — file mode needs no database).

## Next

- Feed the market gate from a live `monitorclt_market` run on the host (it already
  fetches FRED); cache the CSVs so pipeline runs stay deterministic.
- Wire the `leads.offer_queue` view into the CRM lead list and the daily digest
  metrics (`pipeline.offer_ready`, `pipeline.priority_offers`).
