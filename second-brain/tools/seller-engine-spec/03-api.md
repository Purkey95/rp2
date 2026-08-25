# API Contracts v1.0 — frozen at G0

Three surfaces with three different threat models. Blueprint §66–§70.

## Conventions

- All bodies JSON. All times RFC 3339 UTC.
- Write endpoints accept `Idempotency-Key`; replaying a key returns the original
  response with `200` rather than creating a second entity. Blueprint §73.
- Errors: `{"error": {"code": "SNAKE_CASE", "message": "human readable", "field": "…"}}`.
  Codes are stable; messages are not. Never leak PII into a message.
- Every response carries `X-Correlation-Id`.

---

## A. Public seller API — hostile until proven otherwise

Unauthenticated, internet-facing, and the only surface a stranger can reach.
Deliberately small: no property lookup is exposed here. A public "what is this
address worth" endpoint would hand the entire MonitorCLT property graph to
anyone with curl.

```
POST /seller/session
  → 201 {"session_id","correlation_id"}
  Body: {"landing_page_id","utm":{...},"referrer"}
  Rate: 30/hour/IP

POST /seller/session/{id}/property
  → 200 {"status":"ACCEPTED"}          // never returns match results
  Body: {"address":"123 Main St, Charlotte NC 28208"}
  Note: resolution happens asynchronously. Returning candidates here would make
        this endpoint a free parcel-lookup API. Blueprint §49 in spirit.

POST /seller/session/{id}/contact
  → 200 {"status":"ACCEPTED"}
  Body: {"first_name","last_name","phone","email"}

POST /seller/session/{id}/otp/send
  → 202 {"expires_at","send_count"}
  Rate: 3/inquiry, 5/phone/day, 20/IP/day. Each send costs money.
  → 429 {"error":{"code":"OTP_RATE_LIMITED"}}

POST /seller/session/{id}/otp/verify
  → 200 {"verified":true}
  Body: {"code":"123456"}
  Max 5 attempts, then the verification session is burned and must be re-sent.
  → 400 {"error":{"code":"OTP_INVALID","attempts_remaining":2}}

POST /seller/session/{id}/questionnaire
  → 200 {"status":"ACCEPTED"}
  Body: {"form_version":"Q_V1","answers":{"TIMELINE":"UNDER_30_DAYS",...}}

POST /seller/session/{id}/submit
  → 202 {"inquiry_id","status":"SUBMITTED"}
  Body: {"consent":{"consent_text_version":"C_V1","onward_disclosure_shown":true,
                    "privacy_policy_version":"P_V1","terms_version":"T_V1"}}
  REJECTS with CONSENT_REQUIRED if onward_disclosure_shown is false.   [REVIEW F4]
  REJECTS with PHONE_NOT_VERIFIED if the OTP step has not completed.
```

**Security posture:** rate limited, bot-challenged, strict schema validation,
every field length-capped, no enumeration of ids, no property data returned ever.

---

## B. Internal API — authenticated, role-checked, audited

```
GET  /opportunities?status=&priority=&stale_only=&page=
       → paged list, PII redacted unless the caller holds pii:read

GET  /opportunities/{id}
       → full record incl. score explanations and enrichment freshness

POST /opportunities/{id}/review
       Body: {"resolution":"APPROVE|REJECT|MERGE|CLARIFY","reason_code","notes"}
       → writes audit.action_log with before/after. Blueprint §77.

POST /opportunities/{id}/activity
       Body: {"activity_type":"CALL_ATTEMPT","occurred_at","metadata"}

POST /opportunities/{id}/contract
       Body: {"contract_date","contract_price","exit_strategy"}

POST /opportunities/{id}/closing
       Body: {"closing_date","purchase_price","sale_price","assignment_fee",...}

POST /opportunities/{id}/score/override
       Body: {"score_type","value","reason"}
       → appends a NEW lead.score row. Never mutates the prior one. §32.

GET  /health/enrichment
       → per-domain freshness and SLA breach list.                   [REVIEW F3]
```

---

## C. Buyer API — not built until G10

Specified now so the contract is stable; no service implements it before the
marketplace gate.

```
GET  /buyer/leads            GET /buyer/leads/{id}
POST /buyer/leads/{id}/activity
POST /buyer/refunds          GET /buyer/wallet     GET /buyer/transactions
```

Webhooks: `lead.created`, `lead.delivered`, `lead.updated`, `credit.approved`.
Signed with HMAC-SHA256 over the raw body; replayable for 24h by `event_id`.

**Every buyer query is scoped by `organization_id` at the repository layer, with
Postgres row-level security as the second line.** A buyer reaching another
buyer's leads, wallet or refunds is the worst failure this system has. Blueprint §70.
