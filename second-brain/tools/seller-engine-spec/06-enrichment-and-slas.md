# Property Enrichment Field Map & Freshness SLAs

Blueprint §23–§25, amended per **[REVIEW F3]**.

## Why SLAs exist here

The blueprint asks the MonitorCLT data platform for fourteen domains as though
they are dependable. The measured reality on 2026-08-24:

| Pipeline | 7-day success | Age |
|---|---|---|
| `rod_lending_ocr` | 0% | 11 days dark |
| `econdev_proximity` | 0% | — |
| `code_enforcement_sync` | 0% | — |
| `condo.declaration-pulls` | 0% | — |
| `fsbo_byowner` | 38% | — |
| `osha_inspections` | 25% | — |
| FSBO Match (job) | — | 12 days overdue |
| GDELT News Monitor | — | 10 days overdue |

**A stale feed does not lower its own confidence. It keeps returning the last
value it held.** So without a freshness gate, `lead.enrichment_snapshot`
faithfully records eleven-day-old data at high confidence, the scorer consumes
it, and the audit trail makes the result *reproducible* without making it
*correct*. That is a worse failure than a visible outage, because it is silent.

## Field map

| Snapshot bucket | Source domain | Key fields | SLA | On breach |
|---|---|---|---|---|
| `property_features` | Parcel / assessor | `market_value`, `property_type`, `beds`, `baths`, `sqft`, `year_built`, `condo_flag` | 30d | STALE → block PROPERTY, MASTER |
| `ownership_features` | Deeds / ownership | `owner_name`, `entity_owned`, `absentee_owner`, `out_of_state_owner`, `years_owned`, `trust_or_estate` | 7d | STALE → block PROPERTY, MASTER |
| `financial_features` | ROD deed-of-trust OCR, tax | `open_liens`, `mortgage_balance_est`, `estimated_equity`, `free_and_clear`, `tax_delinquent` | 7d | STALE → block PROPERTY, MASTER |
| `distress_features` | Foreclosure, code, WARN | `foreclosure_notice`, `sale_date`, `code_violation`, `vacancy_flag`, `trustee` | 3d | STALE → block PROPERTY, MASTER |
| `market_features` | Market, comps, Zillow heat | `median_sale_price`, `market_heat`, `dom_median`, `comp_count` | 14d | STALE → block MASTER only |

Freshness is computed per domain as `now() - max(observed_at)` compared against
the SLA, producing `FRESH` (< SLA), `AGING` (< 2× SLA), `STALE` (≥ 2× SLA), or
`MISSING` (no rows at all).

**`MISSING` is the default.** An absent freshness entry is never treated as
fresh — see the test `gate: absent freshness is treated as MISSING, not assumed
fresh` in `05-scoring.py`.

## Provenance, per attribute

Every attribute in `source_metadata` carries the full record, so any number can
be defended:

```json
{"estimated_equity": {"value": 221400, "source": "MODEL_EQUITY_V3",
                      "observed_at": "2026-08-12T00:00:00Z",
                      "retrieved_at": "2026-08-24T09:14:02Z",
                      "confidence": 0.82}}
```

This is the difference between *"MonitorCLT says this is true"* and
*"MonitorCLT estimates this with 82% confidence from these inputs, observed
twelve days ago."* Only the second survives a buyer dispute.

## Partial enrichment is normal

Blueprint §74 is right: an unavailable provider must never fail the seller
submission. The orchestrator emits `property.enrichment_completed` with whatever
it has, marks the missing domains, and lets the gate decide. The seller sees
nothing; the operator sees a review task naming the exact domain.
