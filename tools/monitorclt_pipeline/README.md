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

## Next

- Persist the offer sheet to the `leads` table (the score module's DB mode) so the
  CRM and mail/SMS lanes consume it directly.
- Add the market-timing overlay (`monitorclt_market`) as a when/where gate on top of
  the per-parcel ranking.
