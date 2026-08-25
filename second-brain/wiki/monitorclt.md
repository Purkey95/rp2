---
type: entity
created: 2026-08-25
updated: 2026-08-25
tags: [charlotte, real-estate, data-pipeline, proprietary-data]
---

# MonitorCLT

MonitorCLT is a Charlotte/Mecklenburg real estate intelligence pipeline operated
from `cltbuys.com` (alert sender `hello@cltbuys.com`). It runs **53 data sources**
and tracks **155 metrics**, emitting a morning brief, a metrics digest, a data
quality digest, foreclosure-notice alerts, and integration alarms.

Its output also feeds the daily **Local Market Signals — Charlotte** block at the
top of the Daily Research Brief, and it writes leads into **Follow Up Boss** (CRM).

## What it collects

| Stream | Pipeline id | What it yields |
|---|---|---|
| Recorded sales / excise stamps | Register of Deeds | Actual clearing prices, faster than MLS |
| Deed-of-trust OCR | `rod_lending_ocr` | Who is lending, loan sizes, private/hard-money volume |
| Foreclosure notices | `mecktimes`, `hutchens`, `brock_scott`, `gaston_foreclosure` | Pre-auction distress, SP case numbers, sale dates |
| Trustee deeds | — | Completed auction outcomes and clearing prices |
| FSBO detection | `fsbo_byowner` | For-sale-by-owner inventory |
| Rezonings | — | 86 tracked petitions |
| Road + transit | `ncdot_projects`, `econdev_proximity` | 3,825 funded road projects, 83 transit stations |
| Code enforcement | `code_enforcement_sync` | Violations / distressed condition signal |
| Construction safety | `osha_inspections` | Active builders and job sites |
| Condo declarations | `condo.declaration-pulls` | HOA / condo conversion activity |
| Institutional filings | `sec_edgar` | Public-company buyers and REO holders |
| Labor | WARN notices | Layoff-driven distress, 90-day window |
| Sentiment | Zillow heat, Google Trends, GDELT (Ollama-scored), UMich | Buyer/seller intent and news tone |
| Government programs | Programs catalog | 71 active federal/state/local programs, 15 on recurring feeds |

## Signals it produces (snapshot 2026-08-24)

See [[charlotte-market-signals-2026-08]] for the full numbers.

## Known operational state (2026-08-24)

- **Dead:** FSBO Match (12d overdue), ROD deed-of-trust OCR (11d), GDELT News Monitor (10d)
- **Zero 7d success rate:** `rod_lending_ocr`, `econdev_proximity`, `code_enforcement_sync`, `condo.declaration-pulls`
- **Degraded:** `fsbo_byowner` 38%, `osha_inspections` 25%, `sec_edgar` 50%, `ncdot_projects` 50%
- **Contactability crisis:** 2,381 of 2,804 active drip enrollments (85%) have no phone or email;
  `sequence.contactable_pct` is 15.09% against a 50% target
- **Backlog:** 749 pending messages; 302 uncontacted hot leads out of 3,025
- **Infra:** disk 90% used; Telegram ops-alert integration broken
- **Compliance gap:** `EMAIL_PHYSICAL_ADDRESS` unset — outbound email is not CAN-SPAM compliant

Related: [[marketing-strategy-monitorclt]]
