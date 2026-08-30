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

CREATE INDEX ON probate.parcel (county, pin);
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
    reviewer            text,
    reviewed_at         timestamptz,
    review_note         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (left_source, left_id, right_source, right_id),
    -- a confirmed match is a human's signature or an auditable rule run; either
    -- way it must name a person record.
    CHECK (status <> 'confirmed' OR person_id IS NOT NULL OR reviewer IS NOT NULL)
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
       m.flags
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
SELECT m.id, m.left_id, m.right_id, m.match_tier, m.score, m.evidence, m.flags, m.created_at
FROM probate.entity_match m
WHERE m.status = 'pending'
ORDER BY m.score DESC, m.left_id, m.right_id;
