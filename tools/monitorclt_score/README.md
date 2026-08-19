# MonitorCLT Seller Opportunity Score

Turns the wired signals into **ranked, traceable acquisition leads** — not 50
lists, but **five component scores that compose into one Seller Opportunity
Score**:

```
Financial Distress · Property Distress · Ownership Transition ·
Landlord Fatigue · Disposition Probability   →   Seller Opportunity Score 0-100
```

Every signal maps to one dimension (`weights.json` → `dimension_of`). Each
dimension is its own 0-100 component; the combined score is the capped sum of all
contributions; and `dimensions_firing` shows breadth — a lead lit across three
dimensions is more robust than one dimension maxed. Every point traces back to
its source. Instead of *"12,480 absentee owners,"* the output is *"147 parcels
scoring 80+, and here's why each one scored high."*

The full signal taxonomy (150+ signals across the five dimensions) and where each
one's data comes from — wired now / derived / free-to-add / Secretary-of-State /
Register-of-Deeds-browser / court-browser / paid MLS / not-obtainable — is in
`signals_catalog.md`. Config-ready signals score automatically the moment a
source emits them, so adding ROD liens or court records later is a data task, not
a scoring change.

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
   Each contributes its weight to its dimension and keeps its `source_url`.
2. **Derived signals** (from the parcel/owner, no new source): `absentee`,
   `out_of_state`, `long_tenure_20y`, `high_equity_proxy` (only when a mortgage
   figure is present — parcel value alone can't prove equity, so never guessed),
   `large_portfolio`, `recently_sold_another` (the behavioral edge — from entity
   resolution over the owner's portfolio).
3. **Five component scores** — each = capped sum of its dimension's contributions.
4. **Combined Seller Opportunity Score** = capped sum of all contributions;
   `dimensions_firing` = how many of the five lit up.
5. **Bands** — 81-100 immediate · 61-80 priority · 41-60 mail+call ·
   21-40 digital · 0-20 skip.

Weights, the `dimension_of` map, and bands live in `weights.json` — tune freely.

## Output shape

```
123 N Main St   Seller Opportunity Score: 80 [priority]  (4 dimensions)
  components -> financial:30, property:16, ownership:22, disposition:12
  +20 tax_delinquency   +16 code_violation   +12 recently_sold_another
  +10 high_equity_proxy  +8 absentee  +8 out_of_state  +6 long_tenure_20y
```

Each `signals[]` entry carries its `dimension` and `evidence` (source URL or the
computed reason), so a lead's score is fully explainable and auditable.

## Coverage

The score is honest — it scores what's present and shows which dimensions have no
data for a parcel (never a silent zero). What feeds each of the 150+ signal types
today vs. what's a data task away (ROD/court browser, SoS, MLS, etc.) is laid out
in `signals_catalog.md`.

## Data contract

- **signals**: JSONL from the sourcing adapters — needs `signal_type`, `bucket`,
  `apn`, `source_url`.
- **parcels**: CSV/rows from the enrichment parcel table — `apn`, `owner`,
  `situs_street`, `mail_street`/`mail_state`, `property_state`, `sale_year`,
  `assessed_value`, `mortgage_balance` (optional), `vacant`.

## Live database mode (built)

Apply `schema.sql` (creates `leads` + the `priority_leads` view), install the
driver (`pip install "psycopg[binary]"`), then run against the live tables:

```bash
python3 score.py --dsn "postgresql://user:pass@host/monitorclt" --year 2026
```

With `--dsn` it reads active `signals` (from the sourcing adapters) + `parcels`
(from enrichment), scores, and **upserts into `leads`** (idempotent — recomputed
each run, so a lead's score rises as problems stack). `db.py` holds the queries;
adjust the `SELECT` column names to your live schema. Emit
`score.leads_priority_plus` and `score.leads_immediate` (see `schema.sql`) into
the daily digest. Without `--dsn` it runs CSV/JSONL as above.
