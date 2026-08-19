# MonitorCLT Information-Arbitrage Derivations

The sixth of the seven arbitrages: no single record says anything unusual, but two
of them read together reveal value (or a problem) neither shows alone. Both run on
records we already hold.

## Signals

- **`tax_lot_legal_lot_mismatch`** — one **tax** parcel that legally contains
  several recorded **lots**. The assessor bills it as one property, but it can be
  split and sold as separate legal lots with no subdivision process — hidden
  inventory the parcel record doesn't advertise. Lot count comes from an explicit
  `legal_lot_count` field or is parsed from the `legal_description`.
- **`address_anomaly_multiunit`** — the assessor says one unit, but the parcel
  carries multiple distinct addresses (`100 MAIN`, `100 MAIN A`, `100 MAIN B`) or a
  permit describing a duplex/ADU/multi-family. Either hidden income value or an
  unpermitted-use compliance angle — both worth knowing first.

## Run

```bash
python3 info.py --parcels parcels.csv \
  --addresses addresses.jsonl --permits permits.jsonl
python3 test_info.py
```

Both emit parcel-keyed signals the five-score consumes, with evidence. They're
inferences from record cross-reference, so the confidence model marks them
lower-reliability — **a lift, not proof**. The multi-unit anomaly deliberately
does *not* fire when distinct addresses simply match the assessor's unit count
(a real fourplex with four addresses is not an anomaly).
