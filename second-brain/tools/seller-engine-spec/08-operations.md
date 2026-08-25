# Queues, Retries, Agent Permissions & Testing

Blueprint §73–§79, §69–§72.

## Queue and job definitions

Redis Streams behind `EventPublisher` / `EventConsumer` (§6). One consumer group
per service. Consumers must be idempotent on `event_id` — delivery is at-least-once.

| Queue | Consumer | Concurrency | Timeout | Retries | On exhaustion |
|---|---|---|---|---|---|
| `otp.send` | verification-service | 8 | 10s | 3 × exp backoff (2/4/8s) | mark session FAILED, tell the seller to retry |
| `property.resolve` | property-resolution | 4 | 30s | 2 | route to MANUAL_REVIEW (never guess a parcel, §22) |
| `enrichment.domain.*` | one worker per domain | 4 each | 45s | 3 | mark that domain MISSING, continue with partial (§74) |
| `lead.quality` | lead-quality-service | 4 | 20s | 3 | MANUAL_REVIEW |
| `lead.score` | scoring-service | 8 | 10s | 3 | MANUAL_REVIEW |
| `fub.sync` | fub-sync-service | 2 | 30s | 5 × exp backoff | DLQ + ops alert; opportunity still exists locally |
| `delivery.send` | delivery-service | 4 | 30s | 5 | DLQ + ops alert. **Never release the assignment** — retry delivery independently of the sale (§51) |
| `routing.assign` | routing-service | 1 | 15s | 0 | release the reservation, requeue once |

`routing.assign` runs at concurrency **1 per opportunity**, holding a Postgres
advisory lock keyed on `opportunity_id`. Retries are zero because a retried
charge is a double charge; the reservation is released and the opportunity
returns to `ROUTABLE`.

## Error and retry rules

1. **External failure never corrupts the core transaction.** A dead enrichment
   provider yields partial enrichment, not a failed submission.
2. **Retry only what is idempotent.** Sends, charges and assignments carry an
   idempotency key; anything without one is not retried automatically.
3. **Nothing is silently dropped.** Exhausted retries emit `system.dead_letter`
   and raise an ops alert (§75).
4. **Backoff is exponential with jitter**, capped at 60s.
5. **A queue depth above its threshold for 15 minutes is an incident**, not a
   statistic. MonitorCLT already runs a 749-message backlog; that is the failure
   mode to design against.

## Agent tool permissions

Agents get service-level scopes, never admin. Blueprint §71, §78.

| Agent | May | May never |
|---|---|---|
| Property Enrichment | read property.*, request enrichment, summarise evidence | modify scores, touch billing, assign buyers |
| Contact Enrichment | read entity-owned leads, query NC SOS, write `seller.person` contact fields | read full PII of non-entity leads, send outbound messages |
| Quality Review | read inquiry + evidence, write `lead.review_task.recommendation` | set final `resolution`, approve or reject |
| Refund | read refund evidence, write `credit_request.recommendation` | issue money, write to `finance.ledger_entry` |
| Report Generation | read aggregate views, write drafts | read row-level PII, publish without human approval |
| Pipeline Repair | read logs, restart named jobs, open PRs | write to any production table, deploy |

Two rules bind all of them:

- **Structured output only.** Not *"this seems like a good lead"* but
  `{"recommendation":"HIGH_PRIORITY","confidence":0.88,"reasons":[...]}`.
  Structured output can be tested; intuition cannot (§79).
- **Recommend, then a human acts.** Any agent write lands in a `recommendation`
  column, never in a decision column.

## PII rules

`seller.person` is the only table holding names, phones and emails. Everywhere
else carries ids. Enforced by:

- encryption in transit and at rest; deterministic hashes for dedupe joins
- `pii:read` as a distinct role, granted to Operations Manager and above
- redaction in logs, error tracking, and **event payloads** — the catalog's
  `pii_rule` forbids PII in any event body
- coarse geo only in `marketing.session`; the raw IP is never persisted

## Testing requirements

| Layer | Requirement |
|---|---|
| Schema | `01-schema.sql` executes clean on Postgres 16; `01a-constraint-tests.sql` proves exclusive-assignment, idempotency, self-duplicate, enum and ledger guards |
| State machines | `04-state-machines.py` — every state reachable, every non-terminal state can reach a terminal one, every guard maps to a real transition |
| Scoring | `05-scoring.py` — 21 checks incl. the staleness gate; weights sum to 1.0; a suppressed score never becomes P0 |
| Events | every event in `02-events.json` has a declared emitter and at least one consumer; no payload contains PII |
| Routing | **concurrency test is mandatory**: N workers race one exclusive opportunity; exactly one assignment and exactly one ledger debit must exist afterwards |
| Funnel | end-to-end from `POST /seller/session` to `opportunity.qualified` against a seeded property fixture |
| Idempotency | every write endpoint replayed twice returns one entity |
| Authorization | buyer A must receive 404, not 403, for buyer B's leads — 403 confirms existence |

Run everything with `python3 validate_spec.py`.
