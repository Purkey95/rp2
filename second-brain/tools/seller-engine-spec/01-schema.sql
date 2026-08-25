-- =============================================================================
-- MonitorCLT Seller Engine — Data Specification v1.0
-- Target: PostgreSQL 15+ with PostGIS
--
-- FROZEN AT G0. Changes after G0 require a migration and a version bump.
--
-- Three amendments from the blueprint review are folded in here; each is marked
-- [REVIEW Fn] at the point it appears:
--   F2  Follow Up Boss is the system of engagement, not Twenty CRM.
--   F3  Enrichment carries a freshness SLA and scoring gates on staleness.
--   F4  Consent records what the seller was told about onward disclosure.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy address / name matching

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marketing;
CREATE SCHEMA IF NOT EXISTS seller;
CREATE SCHEMA IF NOT EXISTS lead;
CREATE SCHEMA IF NOT EXISTS marketplace;
CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS outcome;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS ml;

-- property.* already exists in the MonitorCLT data platform. The Seller Engine
-- REFERENCES it and never copies it (blueprint §1). property_id below is a soft
-- reference: no FK across the domain boundary, validated at the service layer.


-- =============================================================================
-- ENUMERATED TYPES
-- Enums, not free text, so an invalid state is a database error not a bug.
-- =============================================================================

CREATE TYPE seller.inquiry_status AS ENUM (
    'STARTED', 'PROPERTY_ENTERED', 'CONTACT_ENTERED', 'OTP_PENDING',
    'PHONE_VERIFIED', 'QUESTIONNAIRE_COMPLETE', 'SUBMITTED', 'ENRICHING',
    'QUALITY_REVIEW', 'QUALIFIED',
    -- terminal / diversion
    'ABANDONED', 'INVALID', 'DUPLICATE', 'SPAM', 'REJECTED', 'MANUAL_REVIEW'
);

CREATE TYPE lead.opportunity_status AS ENUM (
    'QUALIFIED', 'ROUTABLE', 'ASSIGNED', 'DELIVERED', 'CONTACT_ATTEMPTED',
    'CONTACTED', 'APPOINTMENT', 'OFFER', 'CONTRACT', 'CLOSED_WON',
    -- alternative exits
    'CLOSED_LOST', 'DISQUALIFIED', 'REFUNDED', 'RETURNED', 'EXPIRED'
);

CREATE TYPE lead.check_status  AS ENUM ('PASS', 'FAIL', 'WARN', 'UNKNOWN');
CREATE TYPE lead.score_type    AS ENUM ('TRUST', 'MOTIVATION', 'PROPERTY', 'MASTER', 'DEAL_PROBABILITY');
CREATE TYPE lead.priority      AS ENUM ('P0', 'P1', 'P2', 'P3');

CREATE TYPE property.resolution_status AS ENUM (
    'EXACT', 'HIGH_CONFIDENCE', 'AMBIGUOUS', 'NOT_FOUND', 'MANUAL_REVIEW'
);

-- [REVIEW F3] Every enrichment domain reports freshness against an SLA.
CREATE TYPE lead.freshness AS ENUM ('FRESH', 'AGING', 'STALE', 'MISSING');

-- [REVIEW F5] Organic is a first-class acquisition channel, not an afterthought.
CREATE TYPE marketing.channel AS ENUM (
    'ORGANIC_SEARCH', 'ORGANIC_REPORT', 'PRESS_CITATION', 'ALERT_LIST',
    'REFERRAL_PARTNER', 'DIRECT', 'SOCIAL_ORGANIC', 'PAID_SEARCH',
    'PAID_SOCIAL', 'PAID_OTHER'
);


-- =============================================================================
-- MARKETING  — campaigns, creatives, sessions, attribution
-- =============================================================================

CREATE TABLE marketing.campaign (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    channel         marketing.channel NOT NULL,
    platform        TEXT,                      -- NULL for organic
    objective       TEXT,
    territory_id    UUID,
    start_at        TIMESTAMPTZ,
    end_at          TIMESTAMPTZ,
    budget_cents    BIGINT DEFAULT 0,          -- 0 for organic; see [REVIEW F5]
    status          TEXT NOT NULL DEFAULT 'DRAFT',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE marketing.creative (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id     UUID REFERENCES marketing.campaign(id),
    type            TEXT NOT NULL,             -- REPORT | ARTICLE | AD | EMAIL | LANDING
    headline        TEXT,
    body            TEXT,
    media_reference TEXT,
    offer           TEXT,
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, type, version)
);

-- The funnel begins here. One row per landing visit, paid or organic.
CREATE TABLE marketing.session (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    correlation_id     UUID NOT NULL,          -- blueprint §8: threads to closing
    channel            marketing.channel NOT NULL,
    campaign_id        UUID REFERENCES marketing.campaign(id),
    creative_id        UUID REFERENCES marketing.creative(id),
    landing_page_id    TEXT,
    utm_source         TEXT,
    utm_medium         TEXT,
    utm_campaign       TEXT,
    utm_content        TEXT,
    utm_term           TEXT,
    platform_click_id  TEXT,                   -- NULL for organic
    referrer           TEXT,
    landing_url        TEXT,
    user_agent_hash    TEXT,                   -- hashed, never raw [REVIEW F4]
    ip_country         TEXT,                   -- coarse only; no raw IP stored
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    converted_at       TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_session_correlation ON marketing.session(correlation_id);
CREATE INDEX idx_session_started  ON marketing.session(started_at DESC);
CREATE INDEX idx_session_campaign ON marketing.session(campaign_id);

CREATE TABLE marketing.attribution_event (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id        UUID NOT NULL REFERENCES marketing.session(id),
    correlation_id    UUID NOT NULL,
    event_type        TEXT NOT NULL,           -- see 02-events.json funnel list
    event_time        TIMESTAMPTZ NOT NULL DEFAULT now(),
    seller_inquiry_id UUID,
    opportunity_id    UUID,
    campaign_id       UUID,
    metadata          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_attr_session ON marketing.attribution_event(session_id, event_time);
CREATE INDEX idx_attr_corr    ON marketing.attribution_event(correlation_id);


-- =============================================================================
-- SELLER  — person, inquiry, questionnaire, verification
-- PII lives here and nowhere else. See 08-operations.md for access rules.
-- =============================================================================

CREATE TABLE seller.person (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name     TEXT,
    last_name      TEXT,
    primary_phone  TEXT,                       -- E.164, encrypted at rest
    primary_email  TEXT,
    phone_hash     TEXT,                       -- deterministic hash for dedupe
    email_hash     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_person_phone_hash ON seller.person(phone_hash);
CREATE INDEX idx_person_email_hash ON seller.person(email_hash);

-- One PERSON may file many INQUIRIES across many PROPERTIES (blueprint §11).
CREATE TABLE seller.inquiry (
    id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    correlation_id         UUID NOT NULL,
    person_id              UUID REFERENCES seller.person(id),
    property_id            BIGINT,             -- soft ref into property.* (§1)
    marketing_session_id   UUID REFERENCES marketing.session(id),
    status                 seller.inquiry_status NOT NULL DEFAULT 'STARTED',
    submitted_address      TEXT,
    normalized_address     TEXT,
    phone_verified         BOOLEAN NOT NULL DEFAULT FALSE,
    consent_record_id      UUID,
    duplicate_probability  NUMERIC(4,3),
    fraud_probability      NUMERIC(4,3),
    idempotency_key        TEXT,               -- blueprint §73
    submitted_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_inquiry_idempotency
    ON seller.inquiry(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_inquiry_status   ON seller.inquiry(status);
CREATE INDEX idx_inquiry_property ON seller.inquiry(property_id);
CREATE INDEX idx_inquiry_person   ON seller.inquiry(person_id);

-- Every status change is a row, never an overwrite (blueprint §10).
CREATE TABLE seller.inquiry_transition (
    id             BIGSERIAL PRIMARY KEY,
    inquiry_id     UUID NOT NULL REFERENCES seller.inquiry(id),
    from_status    seller.inquiry_status,
    to_status      seller.inquiry_status NOT NULL,
    reason_code    TEXT,
    actor          TEXT NOT NULL,              -- service name or user id
    event_id       UUID,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_inq_trans ON seller.inquiry_transition(inquiry_id, occurred_at);

-- Key/value, so the questionnaire evolves without migrations (blueprint §18).
CREATE TABLE seller.questionnaire_response (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id     UUID NOT NULL REFERENCES seller.inquiry(id),
    question_code  TEXT NOT NULL,
    answer_code    TEXT,
    answer_text    TEXT,
    form_version   TEXT NOT NULL,
    answered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (inquiry_id, question_code)
);

CREATE TABLE seller.verification_session (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id          UUID NOT NULL REFERENCES seller.inquiry(id),
    phone               TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_reference  TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count       INT NOT NULL DEFAULT 0,
    send_count          INT NOT NULL DEFAULT 0,
    expires_at          TIMESTAMPTZ NOT NULL,
    verified_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    -- NOTE: the OTP code itself is NEVER stored (blueprint §19).
);
CREATE INDEX idx_verif_inquiry ON seller.verification_session(inquiry_id);


-- =============================================================================
-- AUDIT  — consent and immutable action log
-- =============================================================================

-- [REVIEW F4] Consent must record what the seller was TOLD, including that their
-- details may be passed to a third-party buyer. Entitlement (§49) controls what a
-- buyer CAN see; this records what the seller AGREED to. They are not the same,
-- and only this table is a defence.
CREATE TABLE audit.consent_record (
    id                        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    seller_inquiry_id         UUID NOT NULL REFERENCES seller.inquiry(id),
    consent_type              TEXT NOT NULL,   -- CONTACT | ONWARD_DISCLOSURE | MARKETING
    consent_text_version      TEXT NOT NULL,
    consent_text_hash         TEXT NOT NULL,   -- hash of the exact text shown
    privacy_policy_version    TEXT NOT NULL,
    terms_version             TEXT NOT NULL,
    onward_disclosure_shown   BOOLEAN NOT NULL DEFAULT FALSE,
    tcpa_language_version     TEXT,            -- calls/texts consent, if collected
    marketing_session_id      UUID REFERENCES marketing.session(id),
    source_url                TEXT NOT NULL,
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_consent_inquiry ON audit.consent_record(seller_inquiry_id);

CREATE TABLE audit.action_log (
    id            BIGSERIAL PRIMARY KEY,
    actor_type    TEXT NOT NULL,               -- USER | SERVICE | AGENT
    actor_id      TEXT NOT NULL,
    action        TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    before_state  JSONB,
    after_state   JSONB,
    reason        TEXT,
    correlation_id UUID,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_action_entity ON audit.action_log(entity_type, entity_id, occurred_at DESC);
CREATE INDEX idx_action_actor  ON audit.action_log(actor_type, actor_id, occurred_at DESC);


-- =============================================================================
-- PROPERTY RESOLUTION  — submitted text to a MonitorCLT property_id
-- =============================================================================

CREATE TABLE property.address_resolution (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id            UUID NOT NULL REFERENCES seller.inquiry(id),
    submitted_address     TEXT NOT NULL,
    normalized_address    TEXT,
    candidate_property_id BIGINT,
    confidence_score      NUMERIC(4,3),
    resolution_method     TEXT,                -- EXACT_PARCEL | GEOCODE | TRGM | MANUAL
    resolution_status     property.resolution_status NOT NULL,
    candidates            JSONB NOT NULL DEFAULT '[]',  -- all near-matches, §22
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_addr_res_inquiry ON property.address_resolution(inquiry_id);


-- =============================================================================
-- LEAD  — enrichment snapshots, quality checks, scores, opportunities
-- =============================================================================

-- [REVIEW F3] THE MOST IMPORTANT TABLE IN THE SPEC.
--
-- A snapshot records what we knew when we scored. The blueprint had this right.
-- What it lacked: a stale feed does not lower its own confidence — it keeps
-- returning the last value it held. On 2026-08-24 four MonitorCLT pipelines had
-- been at 0% success for 10-12 days while still serving their last-known values.
-- Without the freshness columns below, a snapshot faithfully records eleven-day-old
-- data at high confidence and the audit trail makes that REPRODUCIBLE, not CORRECT.
--
-- domain_freshness maps each enrichment domain to a lead.freshness value.
-- The scoring service refuses to emit a number when a domain a score depends on
-- is STALE or MISSING. See 05-scoring.py and 06-enrichment-and-slas.md.
CREATE TABLE lead.enrichment_snapshot (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id           UUID NOT NULL REFERENCES seller.inquiry(id),
    property_id          BIGINT,
    snapshot_version     TEXT NOT NULL,        -- e.g. ENRICHMENT_V1
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    property_features    JSONB NOT NULL DEFAULT '{}',
    ownership_features   JSONB NOT NULL DEFAULT '{}',
    financial_features   JSONB NOT NULL DEFAULT '{}',
    distress_features    JSONB NOT NULL DEFAULT '{}',
    market_features      JSONB NOT NULL DEFAULT '{}',

    -- provenance per attribute: {value, source, observed_at, retrieved_at, confidence}
    source_metadata      JSONB NOT NULL DEFAULT '{}',

    -- [REVIEW F3] {"distress": "FRESH", "financial": "STALE", ...}
    domain_freshness     JSONB NOT NULL DEFAULT '{}',
    stale_domains        TEXT[] NOT NULL DEFAULT '{}',
    is_scoreable         BOOLEAN NOT NULL DEFAULT TRUE,
    unscoreable_reason   TEXT
);
CREATE INDEX idx_snapshot_inquiry ON lead.enrichment_snapshot(inquiry_id);
CREATE INDEX idx_snapshot_stale   ON lead.enrichment_snapshot USING GIN(stale_domains);

CREATE TABLE lead.quality_check (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id    UUID NOT NULL REFERENCES seller.inquiry(id),
    check_type    TEXT NOT NULL,               -- the 10 checks, blueprint §26
    status        lead.check_status NOT NULL,
    score         NUMERIC(5,2),
    reason_code   TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}',
    rule_version  TEXT NOT NULL,
    evaluated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (inquiry_id, check_type, rule_version)
);
CREATE INDEX idx_quality_inquiry ON lead.quality_check(inquiry_id);

-- Duplicates are LINKED, never deleted (blueprint §29). A repeat submission is
-- evidence that motivation increased, which is signal, not noise.
CREATE TABLE lead.duplicate_link (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    original_inquiry_id  UUID NOT NULL REFERENCES seller.inquiry(id),
    duplicate_inquiry_id UUID NOT NULL REFERENCES seller.inquiry(id),
    match_probability    NUMERIC(4,3) NOT NULL,
    match_reasons        JSONB NOT NULL DEFAULT '[]',
    review_status        TEXT NOT NULL DEFAULT 'PENDING',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (original_inquiry_id <> duplicate_inquiry_id),
    UNIQUE (original_inquiry_id, duplicate_inquiry_id)
);

-- Scores are APPEND-ONLY. 72 -> 81 -> 89 with timestamps (blueprint §32).
CREATE TABLE lead.score (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id           UUID REFERENCES seller.inquiry(id),
    opportunity_id       UUID,
    score_type           lead.score_type NOT NULL,
    score                NUMERIC(5,2),         -- NULL when not emitted [REVIEW F3]
    score_version        TEXT NOT NULL,        -- MOTIVATION_V1, MASTER_V1
    model_version        TEXT,                 -- NULL while rules-based (§33)
    feature_snapshot_id  UUID REFERENCES lead.enrichment_snapshot(id),
    explanation          JSONB NOT NULL DEFAULT '{}',  -- drivers, +/- contributions
    suppressed_reason    TEXT,                 -- STALE_ENRICHMENT etc.
    calculated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_score_inquiry ON lead.score(inquiry_id, score_type, calculated_at DESC);
CREATE INDEX idx_score_opp     ON lead.score(opportunity_id, score_type, calculated_at DESC);

CREATE TABLE lead.opportunity (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    correlation_id     UUID NOT NULL,
    seller_inquiry_id  UUID NOT NULL UNIQUE REFERENCES seller.inquiry(id),
    person_id          UUID REFERENCES seller.person(id),
    property_id        BIGINT,
    status             lead.opportunity_status NOT NULL DEFAULT 'QUALIFIED',
    trust_score        NUMERIC(5,2),
    motivation_score   NUMERIC(5,2),
    property_score     NUMERIC(5,2),
    master_score       NUMERIC(5,2),
    priority           lead.priority,
    -- [REVIEW F2] Follow Up Boss is the system of engagement, not Twenty.
    fub_person_id      TEXT,
    fub_synced_at      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_opp_status   ON lead.opportunity(status);
CREATE INDEX idx_opp_priority ON lead.opportunity(priority, created_at DESC);
CREATE INDEX idx_opp_fub      ON lead.opportunity(fub_person_id);

CREATE TABLE lead.opportunity_transition (
    id             BIGSERIAL PRIMARY KEY,
    opportunity_id UUID NOT NULL REFERENCES lead.opportunity(id),
    from_status    lead.opportunity_status,
    to_status      lead.opportunity_status NOT NULL,
    reason_code    TEXT,
    actor          TEXT NOT NULL,
    event_id       UUID,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_opp_trans ON lead.opportunity_transition(opportunity_id, occurred_at);

CREATE TABLE lead.review_task (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inquiry_id     UUID REFERENCES seller.inquiry(id),
    opportunity_id UUID REFERENCES lead.opportunity(id),
    issue_type     TEXT NOT NULL,              -- blueprint §38 list
    evidence       JSONB NOT NULL DEFAULT '{}',
    recommendation TEXT,
    status         TEXT NOT NULL DEFAULT 'OPEN',
    assigned_to    TEXT,
    resolved_by    TEXT,
    resolution     TEXT,                       -- APPROVE|REJECT|MERGE|CLARIFY
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);
CREATE INDEX idx_review_open ON lead.review_task(status, created_at) WHERE status = 'OPEN';


-- =============================================================================
-- OUTCOME  — activity, contracts, closings. Blueprint decision #11.
-- This is what closes the learning loop (§86). Do not cut it for schedule.
-- =============================================================================

CREATE TABLE outcome.activity (
    id             BIGSERIAL PRIMARY KEY,
    opportunity_id UUID NOT NULL REFERENCES lead.opportunity(id),
    actor_id       TEXT,
    activity_type  TEXT NOT NULL,              -- CALL_ATTEMPT ... LOST
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    source         TEXT NOT NULL DEFAULT 'FUB', -- [REVIEW F2] synced from FUB
    metadata       JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_activity_opp ON outcome.activity(opportunity_id, occurred_at);

CREATE TABLE outcome.loss_reason (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    opportunity_id UUID NOT NULL REFERENCES lead.opportunity(id),
    reason_code    TEXT NOT NULL,              -- blueprint §56 list
    detail         TEXT,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outcome.contract (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    opportunity_id  UUID NOT NULL REFERENCES lead.opportunity(id),
    contract_date   DATE NOT NULL,
    contract_price  NUMERIC(14,2),
    exit_strategy   TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outcome.closing (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id       UUID NOT NULL REFERENCES outcome.contract(id),
    closing_date      DATE NOT NULL,
    purchase_price    NUMERIC(14,2),
    sale_price        NUMERIC(14,2),
    assignment_fee    NUMERIC(14,2),
    gross_profit      NUMERIC(14,2),
    marketing_cost    NUMERIC(14,2) DEFAULT 0, -- 0 for organic [REVIEW F5]
    lead_cost         NUMERIC(14,2) DEFAULT 0,
    net_contribution  NUMERIC(14,2),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =============================================================================
-- MARKETPLACE + FINANCE
-- Specified at G0 so the contracts are stable, but NOT BUILT until G10
-- (blueprint §82, §84). Tables exist; no service writes to them before that gate.
-- =============================================================================

CREATE TABLE marketplace.organization (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    legal_name         TEXT NOT NULL,
    display_name       TEXT,
    billing_account_id UUID,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE marketplace.buyer (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES marketplace.organization(id),
    status          TEXT NOT NULL DEFAULT 'PENDING',
    credit_status   TEXT NOT NULL DEFAULT 'OK',
    billing_status  TEXT NOT NULL DEFAULT 'OK',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE marketplace.territory (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    territory_type        TEXT NOT NULL,       -- STATE|METRO|COUNTY|ZIP|CUSTOM
    geographic_identifier TEXT NOT NULL,
    geometry              GEOGRAPHY(MULTIPOLYGON, 4326),
    parent_id             UUID REFERENCES marketplace.territory(id),
    UNIQUE (territory_type, geographic_identifier)
);
CREATE INDEX idx_territory_geom ON marketplace.territory USING GIST(geometry);

CREATE TABLE marketplace.buy_box (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    buyer_id              UUID NOT NULL REFERENCES marketplace.buyer(id),
    min_property_value    NUMERIC(14,2),
    max_property_value    NUMERIC(14,2),
    min_equity            NUMERIC(14,2),
    max_price             NUMERIC(14,2),
    allowed_property_types TEXT[],
    min_motivation_score  NUMERIC(5,2),
    min_property_score    NUMERIC(5,2),
    allow_active_listing  BOOLEAN NOT NULL DEFAULT FALSE,
    allow_tenant_occupied BOOLEAN NOT NULL DEFAULT TRUE,
    daily_cap             INT,
    weekly_cap            INT,
    monthly_cap           INT,
    active                BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE marketplace.buyer_territory (
    buyer_id        UUID NOT NULL REFERENCES marketplace.buyer(id),
    territory_id    UUID NOT NULL REFERENCES marketplace.territory(id),
    priority        INT NOT NULL DEFAULT 100,
    exclusivity_type TEXT NOT NULL DEFAULT 'SHARED',
    start_at        TIMESTAMPTZ,
    end_at          TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (buyer_id, territory_id)
);

-- Every routing decision is recorded, including the rejections, so "why didn't I
-- get this lead?" is answerable (blueprint §45).
CREATE TABLE marketplace.routing_decision (
    id             BIGSERIAL PRIMARY KEY,
    opportunity_id UUID NOT NULL REFERENCES lead.opportunity(id),
    buyer_id       UUID NOT NULL REFERENCES marketplace.buyer(id),
    eligible       BOOLEAN NOT NULL,
    reason         TEXT NOT NULL,
    rank           INT,
    price_cents    BIGINT,
    rule_version   TEXT NOT NULL,
    evaluated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_routing_opp ON marketplace.routing_decision(opportunity_id);

CREATE TABLE marketplace.assignment (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    opportunity_id   UUID NOT NULL REFERENCES lead.opportunity(id),
    buyer_id         UUID NOT NULL REFERENCES marketplace.buyer(id),
    exclusivity_type TEXT NOT NULL,
    price_cents      BIGINT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'RESERVED',
    idempotency_key  TEXT,
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ
);
-- Blueprint §44: an EXCLUSIVE opportunity can be assigned exactly once. The
-- partial unique index enforces at the database level what the lock enforces at
-- the application level. Belt and braces, because selling one exclusive lead
-- twice is the single most damaging bug this system can have.
CREATE UNIQUE INDEX idx_assignment_exclusive
    ON marketplace.assignment(opportunity_id)
    WHERE exclusivity_type = 'EXCLUSIVE' AND status <> 'RELEASED';
CREATE UNIQUE INDEX idx_assignment_idem
    ON marketplace.assignment(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE marketplace.delivery (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id      UUID NOT NULL REFERENCES marketplace.assignment(id),
    channel            TEXT NOT NULL,          -- PORTAL|EMAIL|SMS|CRM|WEBHOOK|API
    destination        TEXT,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count      INT NOT NULL DEFAULT 0,
    provider_reference TEXT,
    last_error         TEXT,
    sent_at            TIMESTAMPTZ,
    confirmed_at       TIMESTAMPTZ
);
CREATE INDEX idx_delivery_pending ON marketplace.delivery(status) WHERE status <> 'CONFIRMED';

-- Balance is SUM(ledger), never a mutable column (blueprint §50).
CREATE TABLE finance.ledger_entry (
    id             BIGSERIAL PRIMARY KEY,
    buyer_id       UUID NOT NULL REFERENCES marketplace.buyer(id),
    transaction_type TEXT NOT NULL,            -- DEPOSIT|LEAD_PURCHASE|REFUND_CREDIT|ADJUSTMENT
    amount_cents   BIGINT NOT NULL,            -- signed
    reference_type TEXT,
    reference_id   TEXT,
    idempotency_key TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_buyer ON finance.ledger_entry(buyer_id, created_at DESC);
CREATE UNIQUE INDEX idx_ledger_idem
    ON finance.ledger_entry(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE VIEW finance.buyer_balance AS
    SELECT buyer_id, COALESCE(SUM(amount_cents), 0) AS balance_cents
    FROM finance.ledger_entry GROUP BY buyer_id;

CREATE TABLE finance.autofund_rule (
    buyer_id                 UUID PRIMARY KEY REFERENCES marketplace.buyer(id),
    threshold_cents          BIGINT NOT NULL,
    replenish_cents          BIGINT NOT NULL,
    payment_method_reference TEXT,
    active                   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE finance.credit_request (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id UUID NOT NULL REFERENCES marketplace.assignment(id),
    buyer_id      UUID NOT NULL REFERENCES marketplace.buyer(id),
    reason_code   TEXT NOT NULL,               -- blueprint §53 list
    description   TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}', -- auto-assembled, §54
    recommendation TEXT,                       -- APPROVE|DENY|REVIEW (agent, §78)
    status        TEXT NOT NULL DEFAULT 'PENDING',
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by   TEXT,                        -- a HUMAN. Agents recommend only.
    reviewed_at   TIMESTAMPTZ
);


-- =============================================================================
-- ML  — model registry (blueprint §80). Empty until G9+.
-- =============================================================================

CREATE TABLE ml.model_version (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name       TEXT NOT NULL,
    version          TEXT NOT NULL,
    training_dataset TEXT,
    training_period  TEXT,
    features         JSONB NOT NULL DEFAULT '[]',
    metrics          JSONB NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'CANDIDATE',
    deployed_at      TIMESTAMPTZ,
    retired_at       TIMESTAMPTZ,
    UNIQUE (model_name, version)
);
