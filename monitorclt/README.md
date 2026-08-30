# MonitorCLT — parcel enrichment

The parcel + owner index for Mecklenburg County, NC. This is **Step 1** of the
plan in `../second-brain/wiki/mecklenburg-foreclosure-slice.md`: the spine that
every property-keyed source joins to, and that person-keyed sources (obituaries,
probate, bankruptcy) later match *against*.

Standard library only — no dependency install, no network access needed for the
tests.

## Why this exists first

Sources divide by whether the record already names a parcel
(`../second-brain/wiki/property-keyed-vs-person-keyed-sources.md`). Everything
person-keyed is blocked on having an owner index to match against, so the index
is built before anything that needs it.

## Usage

```sh
python -m monitorclt.cli load      --db parcels.db            # ~428k records, ~3.5 min
python -m monitorclt.cli load      --db parcels.db --limit 20000
python -m monitorclt.cli stats     --db parcels.db
python -m monitorclt.cli address   --db parcels.db "210 N Church St Unit 1104"
python -m monitorclt.cli parcel    --db parcels.db 07848364
python -m monitorclt.cli person    --db parcels.db "Walter H Conrad" --city CHARLOTTE
python -m monitorclt.cli decedents --db parcels.db
python -m monitorclt.cli absentee  --db parcels.db --out-of-state

python -m monitorclt.cli load-sales --db parcels.db          # ~1.5M sales, ~17 min
python -m monitorclt.cli backtest   --db parcels.db --as-of 2022-01-01 --horizon-years 2
python -m monitorclt.cli propensity --db parcels.db   # fit + out-of-time validation
```

## Layout

| Module | Responsibility |
|---|---|
| `normalize/text.py` | Whitespace, case, word-boundary tokenization |
| `normalize/address.py` | Address → stable join key |
| `normalize/owner.py` | Owner string → type + person names |
| `parcel/model.py` | The `Parcel` record and its derived fields |
| `parcel/source.py` | ArcGIS feature-layer client with paging + retry |
| `parcel/store.py` | SQLite index with provenance and `observed_at` |
| `parcel/loader.py` | Wires source → model → store |
| `parcel/enrich.py` | Lookups; person→parcel candidate generation |
| `sales/` | Sales history ingest — the outcome data |
| `backtest/reconstruct.py` | Owner state as of a past date, from the sales chain |
| `backtest/signals.py` | Signal definitions, plus what cannot be backtested |
| `backtest/harness.py` | Precision / recall / lift against outcomes |
| `propensity/model.py` | Transparent cell model over (owner type, tenure) |
| `propensity/validate.py` | Out-of-time decile validation |

## Five things the live data settled

Each of these was found by querying the real service, and each would have been
a silent bug:

1. **`pid` is not a unique key.** 428,504 records carry 396,310 distinct `pid`
   values, because condo units in a building share one. `camapid` and
   `propertyid` are distinct. Keying on `pid` collapses ~32,000 units.
2. **The county's first/last name split is unreliable.** It splits without
   understanding the string, so `'THE GELPI LIVING TRUST'` is stored as
   last=`'THE GELPI LIVING '` / first=`'TRUST'`. The split columns are used only
   after `full_owner_name` classifies as a person.
3. **Substring matching on owner strings is wrong far more often than right.**
   `LIKE '%ESTATE%'` returns 1,119 "REAL ESTATE" companies against ~57 genuine
   estate owners. "LIFE" hits `GRACELIFE CHURCH`; "ETAL" hits
   `VETAL DONALD III`. Every marker is matched on word-boundary tokens.
4. **`UNINC` occupies the jurisdiction slot** for unincorporated addresses
   (`' GOODMAN RD UNINC NC'`) on ~5.5% of parcels, and must be stripped like a
   city name.
5. **Mecklenburg's street suffixes are not USPS.** The county writes AV, CR, BV,
   WY, PY, TR where USPS writes AVE, CIR, BLVD, WAY, PKWY, TRL. Both forms
   canonicalize to the same key, or no cross-source address join works.

## The backtest found the premise backwards

Run 2026-08-29 over 1,492,220 sales at four as-of dates. Long tenure
**anti-predicts** a sale — lift 0.44–0.49x on a 20-year hold, stable across
2012, 2016, 2019 and 2022 — and the control signal (bought within 3 years)
beats every tenure signal. The estate outcome label is unusable: the county
grantor field records only 13–33 estate sales a year.

Full write-up and what it does and does not kill:
`../second-brain/wiki/backtest-results-2026-08.md`.

Reconstruction works from the sales chain alone, never from current CAMA
fields, because CAMA's owner and last-sale-date already reflect the sale being
predicted. That leak is the single easiest way to produce a beautiful and
meaningless backtest.

## The propensity model works and ranks the wrong people

Trained 2016-18, validated out of time on 2022-24: **2.53x lift on the top
decile**, capturing 25% of arms-length sales. Real, and reproducible with
`cli propensity`.

But deciles 2-7 are flat at ~1.0x, so it is one useful cut and then noise;
absolute probabilities do not survive a market regime change (predicted
14.71% vs actual 10.24% in decile 2); and the top decile is
`COMPANY, 0-3 years` — investor inventory being flipped. The model learned
that recently-transacted property transacts again.

Those are the worst leads for the intended business. Keep the model as a
baseline any distress-driven score must beat, and as an exclusion filter.
Write-up: `../second-brain/wiki/propensity-scoring-findings.md`.

## What this does not do

- **No opportunity score.** Enrichment emits facts
  (`evidence_summary()`), and person matching emits candidates with evidence and
  a corroboration tier. Nothing emits a number, because nothing has been
  backtested yet — see
  `../second-brain/wiki/property-signal-scoring-and-calibration.md`.
- **No auto-promotion of person matches.** `Candidate` has no `is_match` field.
  A `STRONG` tier means "exact name plus at least one independent corroborating
  signal", not "this is the person".
- **No outreach, and no verified access terms.** Terms of use for this service
  have not been checked; see
  `../second-brain/wiki/distressed-property-outreach-compliance.md` before
  running scheduled full extracts.

## Tests

```sh
python tests/test_normalize_address.py
python tests/test_normalize_owner.py
python tests/test_parcel_index.py
python tests/test_source.py
```

`tests/fixtures/mecklenburg_cama_sample.json` holds 35 real records covering the
awkward cases above. `pytest` from the repository root also collects them.
