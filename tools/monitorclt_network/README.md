# MonitorCLT Network Intelligence

Two derivations that look past a single property — at the **owner's whole
portfolio** and the **block around it**. Both compute from data we already have
(active signals + the parcel table), no new source.

## owner_network_distress

The insight from the vision docs: one distressed property is noise; several
across the *same owner's* portfolio is a story. Groups parcels by owner and, when
**≥2 of an owner's parcels carry active distress**, emits `owner_network_distress`
on every parcel that owner holds — so when Smith Holdings LLC's A, C, and F all go
sideways, its remaining parcels surface too (disposition dimension).

## neighborhood_contagion

Score micro-markets, not just parcels. Aggregates active distress by area (ZIP for
v1); a `contagion` score = distressed-parcel density × variety of distress types.
Parcels in a **hot** area (≥N distressed) get a `neighborhood_contagion` signal
(property dimension) — flagging a turning block before most investors notice.

## Run

```bash
python3 network.py --signals signals.jsonl --parcels parcels.csv --out network_signals.jsonl
python3 test_network.py
```

Emits parcel-keyed signals (already weighted in the score) plus a per-area
contagion report. Both scores are marked lower-reliability in the confidence model
(they're derived aggregates, not a single verified record), so they lift a lead
but don't masquerade as hard evidence.

## Refinements (next)

- **Radius/geohash** instead of ZIP (needs the layers' lat/lng) for true 1,000-ft
  contagion pockets.
- **Time trend** — "code cases up 31% in 12 months" — which the temporal engine
  already models; feed dated events here to score *rising* contagion, not just
  current level.
- **Contractor / lender networks** — same group-by-network pattern keyed on the
  permit contractor field and ROD deed-of-trust lender (pending those sources).
