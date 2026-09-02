-- MonitorCLT probate -> real property cross-reference: PostgreSQL schema.
--
-- Design rule: the source tables are systems of record and are never mutated by
-- matching. Every assertion that "record A and record B are the same person"
-- lives in probate.entity_match, carries the evidence that produced it, and is
-- reviewable and reversible. Nothing downstream reads a join it cannot explain.
--
-- Deliberately absent: incarceration / probation / parole / arrest data. It is
-- not a lead source, not a lead score, and not a segmentation field here, so
-- there is no table for it to land in. See README.md ("What this does not do").

CREATE SCHEMA IF NOT EXISTS probate;

-- ---------------------------------------------------------------- sources ---
-- One table per system of record. retrieved_at + source_url make every row
-- traceable back to the public record it came from.

CREATE TABLE probate.estate_case (
    id                  bigserial PRIMARY KEY,
    county              text        NOT NULL,
    file_number         text        NOT NULL,   -- Clerk of Superior Court estate file no.
    decedent_name       text        NOT NULL,
    date_of_death       date,
    filing_date         date,
    case_status         text,
    personal_rep_name   text,                   -- executor / administrator
    pr_mailing_address  text,
    source_url          text,
    retrieved_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (county, file_number)
);

CREATE TABLE probate.parcel (
    id                  bigserial PRIMARY KEY,
    county              text        NOT NULL,
    pin                 text        NOT NULL,   -- tax parcel id / PIN
    situs_address       text,
    owner_name          text        NOT NULL,   -- verbatim assessor owner string
    owner_mailing_address text,
    land_use            text,
    assessed_value      numeric(14, 2),
    deed_book           text,
    deed_page           text,
    last_sale_date      date,
    source_url          text,
    retrieved_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (county, pin)
);

CREATE TABLE probate.deed (
    id                  bigserial PRIMARY KEY,
    county              text        NOT NULL,
    instrument_number   text,
    book                text,
    page                text,
    recorded_date       date,
    instrument_type     text,                   -- WD, QC, EXECUTOR DEED, ...
    grantor_name        text,
    grantee_name        text,
    parcel_pin          text,                   -- as printed on the instrument
    source_url          text,
    retrieved_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (county, book, page, instrument_number)
);

-- NC Secretary of State business registration. Statewide, so no county column:
-- an SOS id is county-independent. This is how a decedent who held property
-- through an LLC or corporation becomes reachable at all -- the parcel's owner
-- string names the entity, the SOS names the people who ran it. A match made
-- this way is an interest in the entity, not the parcel, and is never
-- auto-confirmed (see entity_match below).
CREATE TABLE probate.business_entity (
    id                       bigserial PRIMARY KEY,
    sos_id                   text        NOT NULL UNIQUE,
    entity_name              text        NOT NULL,   -- verbatim SOS name
    entity_type              text,
    status                   text,
    domestic                 boolean,
    formation_date           date,
    principal_office_address text,
    mailing_address          text,
    registered_agent_name    text,                   -- kept here, never in entity_official
    registered_agent_address text,                   -- never used for corroboration: usually a law office
    source_url               text,
    retrieved_at             timestamptz NOT NULL DEFAULT now()
);

-- Officers, members, managers, partners, as the SOS filing listed them.
CREATE TABLE probate.entity_official (
    id                  bigserial PRIMARY KEY,
    entity_id           bigint      NOT NULL REFERENCES probate.business_entity (id) ON DELETE CASCADE,
    person_name         text        NOT NULL,   -- verbatim
    title               text,
    source              text        NOT NULL,   -- the filing that listed them, e.g. 'ANNUAL REPORT 2025'
    retrieved_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, person_name, title, source)
);

CREATE INDEX ON probate.parcel (county, pin);
CREATE INDEX ON probate.entity_official (person_name);
CREATE INDEX ON probate.deed (county, parcel_pin);
CREATE INDEX ON probate.estate_case (county, filing_date);

-- ---------------------------------------------------------------- people ----
-- A canonical person exists only once a match has been confirmed. Until then
-- the source rows stand alone -- an unconfirmed candidate never creates an
-- identity.

CREATE TABLE probate.person (
    id                  bigserial PRIMARY KEY,
    normalized_name     text        NOT NULL,   -- FIRST MIDDLE LAST, punctuation stripped
    display_name        text        NOT NULL,
    is_organization     boolean     NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE probate.person_alias (
    person_id           bigint      NOT NULL REFERENCES probate.person (id) ON DELETE CASCADE,
    alias_normalized    text        NOT NULL,
    source              text        NOT NULL,
    PRIMARY KEY (person_id, alias_normalized, source)
);

CREATE TABLE probate.person_address (
    person_id           bigint      NOT NULL REFERENCES probate.person (id) ON DELETE CASCADE,
    address_normalized  text        NOT NULL,
    address_kind        text        NOT NULL,   -- situs | tax_mailing | court_filing
    observed_at         date,
    PRIMARY KEY (person_id, address_normalized, address_kind)
);

-- ---------------------------------------------------------------- matching --

CREATE TYPE probate.match_status AS ENUM ('confirmed', 'pending', 'rejected');

CREATE TABLE probate.match_run (
    id                  bigserial PRIMARY KEY,
    started_at          timestamptz NOT NULL DEFAULT now(),
    tool_version        text        NOT NULL,
    rules_version       text        NOT NULL,
    params              jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- One row per candidate identity assertion between two source records.
-- score/tier/evidence are the *rationale*: why the matcher believes it, and
-- what a reviewer should check. status starts at whatever the rules decided and
-- is overwritten by a human, whose name and timestamp are kept.
CREATE TABLE probate.entity_match (
    id                  bigserial PRIMARY KEY,
    run_id              bigint      REFERENCES probate.match_run (id),
    left_source         text        NOT NULL,   -- 'estate_case'
    left_id             text        NOT NULL,   -- county/file_number
    right_source        text        NOT NULL,   -- 'parcel'
    right_id            text        NOT NULL,   -- county/pin
    person_id           bigint      REFERENCES probate.person (id),  -- set on confirm
    match_tier          text        NOT NULL,
    score               numeric(4, 3) NOT NULL CHECK (score >= 0 AND score <= 1),
    evidence            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    flags               jsonb       NOT NULL DEFAULT '[]'::jsonb,
    status              probate.match_status NOT NULL DEFAULT 'pending',
    via_source          text,                       -- 'business_entity' when reached through an entity
    via_id              text,                       -- that entity's sos_id
    reviewer            text,
    reviewed_at         timestamptz,
    review_note         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (left_source, left_id, right_source, right_id),
    -- a confirmed match is a human's signature or an auditable rule run; either
    -- way it must name a person record.
    CHECK (status <> 'confirmed' OR person_id IS NOT NULL OR reviewer IS NOT NULL),
    CHECK ((via_source IS NULL) = (via_id IS NULL)),
    -- A parcel reached through an entity or a named trustee is held by the entity
    -- or the trust; the estate's interest in it is a legal question. A rule run
    -- can never confirm such a row -- only a signed human review can. The matcher
    -- enforces this from evidence (crossref.CAP_AT_PENDING); this is the second
    -- wall, for anything that writes the table without going through it.
    CHECK (
        status <> 'confirmed'
        OR reviewed_at IS NOT NULL
        OR NOT (flags ?| ARRAY['held_via_entity', 'held_in_trust'])
    )
);

CREATE INDEX ON probate.entity_match (status, score DESC);
CREATE INDEX ON probate.entity_match (left_source, left_id);

-- ---------------------------------------------------------------- views -----

-- The lead list. Confirmed links only: a pending candidate is a question, not a
-- lead, and must not reach outreach tooling.
CREATE VIEW probate.v_estate_property AS
SELECT e.county,
       e.file_number,
       e.decedent_name,
       e.date_of_death,
       e.filing_date,
       e.case_status,
       e.personal_rep_name,
       e.pr_mailing_address,
       p.pin,
       p.situs_address,
       p.owner_name,
       p.land_use,
       p.assessed_value,
       m.match_tier,
       m.score,
       m.evidence,
       m.flags,
       m.via_source,       -- non-null only after a human confirmed an entity-held row
       m.via_id
FROM probate.entity_match m
JOIN probate.estate_case e
  ON m.left_source = 'estate_case'
 AND e.county || '/' || e.file_number = m.left_id
JOIN probate.parcel p
  ON m.right_source = 'parcel'
 AND p.county || '/' || p.pin = m.right_id
WHERE m.status = 'confirmed';

-- What a human still has to look at, worst-corroborated last.
CREATE VIEW probate.v_review_queue AS
SELECT m.id, m.left_id, m.right_id, m.match_tier, m.score, m.evidence, m.flags,
       m.via_source, m.via_id, m.created_at
FROM probate.entity_match m
WHERE m.status = 'pending'
ORDER BY m.score DESC, m.left_id, m.right_id;
