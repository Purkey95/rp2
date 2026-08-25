---
type: synthesis
created: 2026-08-25
updated: 2026-08-25
tags: [architecture, seller-engine, monitorclt, specification]
---

# Seller Engine Data & Workflow Specification v1.0

The level immediately before code, written so several coding agents working
independently produce compatible components rather than inventing their own
contracts. Lives in `second-brain/tools/seller-engine-spec/`.

Follows [[seller-engine-blueprint-review]], and folds that review's amendments
into the contracts rather than leaving them as commentary.

## What it contains

| File | Fixes in stone |
|---|---|
| `01-schema.sql` | 35 tables across 10 schemas, enums, indexes, integrity guards |
| `01a-constraint-tests.sql` | Proves the guards hold, with real inserts |
| `02-events.json` | 22 events, one envelope, the PII rule |
| `03-api.md` | Seller / internal / buyer contracts, three threat models |
| `04-state-machines.py` | Legal transitions and guards — executable |
| `05-scoring.py` | Scoring formulas v1 and the staleness gate — executable |
| `06-enrichment-and-slas.md` | Field map, freshness SLAs, per-attribute provenance |
| `07-wireframes.md` | Seller funnel and ops console |
| `08-operations.md` | Queues, retries, agent permissions, PII, testing |
| `09-dependency-graph.md` | Build order and critical path |

## Verified, not asserted

- The DDL **executes against a real PostgreSQL 16** — 36 tables created clean.
  PostGIS was stubbed locally (extension unavailable in the authoring
  environment), so the single GIST index on `marketplace.territory.geometry`
  remains unverified and needs a check on first real deploy.
- Four integrity guards were exercised with live inserts and correctly rejected:
  selling one exclusive lead twice, a replayed idempotency key, an inquiry
  marked as its own duplicate, and an invalid status value.
- `validate_spec.py` runs 13 cross-checks — including SQL enums against the
  Python state machines, which is exactly where a spec silently drifts.
- 21 scoring checks and both state machines pass.

## The three amendments carried in

- **F2** — Follow Up Boss is the system of engagement; `lead.opportunity` stays
  the system of record. Removes a CRM migration from the critical path.
- **F3** — per-domain freshness SLAs, and a score whose inputs are stale is not
  emitted. Demonstrated: an identical seller scores MASTER 94.3 / P0 on fresh
  data, and is suppressed to `MANUAL_REVIEW` / P2 when the distress feed has
  been dark eleven days.
- **F4** — consent records whether onward disclosure was shown, and hashes the
  exact text. Submission is rejected without it.

F1 became the G-1 gate and F5 the G9.5 gate; neither is a schema change.

Related: [[seller-engine-blueprint-review]] · [[marketing-strategy-monitorclt]] · [[monitorclt]]
