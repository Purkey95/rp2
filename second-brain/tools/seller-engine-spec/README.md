# MonitorCLT Seller Engine — Data & Workflow Specification v1.0

The level immediately before code. Blueprint v2.0 described *what* the system is;
this describes it precisely enough that several coding agents working
independently produce compatible components instead of inventing their own
contracts.

**Status:** draft for the G0 freeze review. Nothing here is frozen until that
review passes.

## Read in this order

| File | What it fixes in stone |
|---|---|
| `01-schema.sql` | 35 tables across 10 schemas, with enums, indexes and the guards that matter |
| `01a-constraint-tests.sql` | Proves the guards actually hold — exclusive assignment, idempotency, enums, ledger |
| `02-events.json` | 22 events, one envelope, the PII rule, the funnel event list |
| `03-api.md` | Seller / internal / buyer contracts and their three different threat models |
| `04-state-machines.py` | Legal transitions and their guards — **executable** |
| `05-scoring.py` | Scoring formulas v1 and the staleness gate — **executable, 21 tests** |
| `06-enrichment-and-slas.md` | Field map, freshness SLAs, provenance |
| `07-wireframes.md` | Seller funnel and ops console layouts |
| `08-operations.md` | Queues, retries, agent permissions, PII rules, testing requirements |
| `09-dependency-graph.md` | Build order, critical path, what starts now |

## Verify it

```bash
python3 validate_spec.py          # everything machine-checkable
python3 05-scoring.py --demo      # three worked leads, incl. the stale case
python3 04-state-machines.py --mermaid
```

The schema was executed against a real PostgreSQL 16 during authoring, not just
eyeballed. PostGIS is stubbed in local validation because the extension is not
installed in the authoring environment — the one GIST index on
`marketplace.territory.geometry` is therefore unverified and needs a check on
first real deploy.

## The three amendments

This spec is not a neutral transcription of the blueprint. Three findings from
the blueprint review are folded in, marked `[REVIEW Fn]` at every point they
appear:

- **F2 — Follow Up Boss, not Twenty CRM.** FUB already holds 3,025 leads and a
  live agent workflow. `lead.opportunity` is the system of record; FUB is the
  system of engagement. Removes a CRM migration from the critical path.
- **F3 — the staleness gate.** Enrichment carries per-domain freshness against an
  SLA, and a score whose inputs are stale is **not emitted** — the opportunity
  routes to `MANUAL_REVIEW` with `STALE_ENRICHMENT`. Without this, four
  pipelines that have been dark for eleven days get snapshotted at full
  confidence and scored anyway.
- **F4 — consent records onward disclosure.** `audit.consent_record` hashes the
  exact text shown and stores whether the seller was told their details may go
  to a buyer. Submission is rejected without it.

F1 (fix the leak first) is the new G−1 gate. F5 (name where zero-cost ends) is
the new G9.5 gate. Neither is a schema change.

## What is deliberately not specified

Dynamic pricing, lead auctions, shared-lead splitting, nationwide geography,
autonomous ad-budget management, and AI negotiating with sellers. Blueprint §82
was right to exclude them and this spec does not smuggle them back in.
