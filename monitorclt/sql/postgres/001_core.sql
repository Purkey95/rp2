-- MonitorCLT core schema (PostgreSQL deployment target; the SQLite file is the reference).
--
-- Three rules shape everything here:
--   1. Sources are immutable and append-only. A record is never updated in place;
--      a new version is written and the old one is superseded (system time), and
--      each version carries the date the source itself asserts (valid time).
--   2. Every identity assertion lives in entity_match with the evidence that
--      produced it, a calibrated probability, and the gates it passed or failed.
--   3. Nothing leaves the system except through the policy layer, and every exit
--      is logged.
--
-- Deliberately absent: incarceration / probation / parole / arrest data. Not a
-- lead source, not a score, not a segmentation field; there is no table for it.

-- ----------------------------------------------------------------- raw -------
-- What was actually fetched, byte for byte. Parsers read from here, so a parser
-- bug is a replay, never a re-scrape.
CREATE TABLE IF NOT EXISTS raw_capture (
    id              bigserial PRIMARY KEY,
    source          TEXT NOT NULL,
    county          TEXT NOT NULL,
    url             TEXT,
    fetched_at      timestamptz NOT NULL,
    content_type    TEXT,
    content_hash    TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    body            bytea NOT NULL,
    UNIQUE (source, county, content_hash)
);

CREATE TABLE IF NOT EXISTS ingest_run (
    id              bigserial PRIMARY KEY,
    connector       TEXT NOT NULL,
    county          TEXT NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    status          TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
    rows_seen       INTEGER NOT NULL DEFAULT 0,
    rows_new        INTEGER NOT NULL DEFAULT 0,
    rows_changed    INTEGER NOT NULL DEFAULT 0,
    rows_retired    boolean NOT NULL DEFAULT false,
    rows_failed     INTEGER NOT NULL DEFAULT 0,
    field_set       jsonb NOT NULL DEFAULT '[]'::jsonb,        -- JSON: fields seen, for drift detection
    watermark_before TEXT,
    watermark_after TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS watermark (
    connector       TEXT NOT NULL,
    county          TEXT NOT NULL,
    value           TEXT,
    updated_at      timestamptz NOT NULL,
    PRIMARY KEY (connector, county)
);

-- ------------------------------------------------------------- history ------
-- One row per version of a source record. observed_at/superseded_at are system
-- time (when we knew it); effective_date is valid time (when the source says it
-- became true). The current version has superseded_at IS NULL.
CREATE TABLE IF NOT EXISTS record_version (
    id              bigserial PRIMARY KEY,
    source          TEXT NOT NULL,
    county          TEXT NOT NULL,
    natural_key     TEXT NOT NULL,
    version_no      INTEGER NOT NULL,
    observed_at     timestamptz NOT NULL,
    superseded_at   timestamptz,
    effective_date  date,
    retired         boolean NOT NULL DEFAULT false,
    content_hash    TEXT NOT NULL,
    payload         jsonb NOT NULL,
    raw_capture_id  bigint REFERENCES raw_capture (id),
    ingest_run_id   bigint REFERENCES ingest_run (id),
    UNIQUE (source, county, natural_key, version_no)
);
CREATE INDEX IF NOT EXISTS ix_record_current ON record_version (source, county, superseded_at, natural_key);
CREATE INDEX IF NOT EXISTS ix_record_key ON record_version (source, county, natural_key);
CREATE INDEX IF NOT EXISTS ix_record_observed ON record_version (observed_at);

-- The change stream. record_* events come from history; domain events come from
-- the source adapter that understands what a change means.
CREATE TABLE IF NOT EXISTS event (
    id              bigserial PRIMARY KEY,
    kind            TEXT NOT NULL,
    source          TEXT,
    county          TEXT,
    natural_key     TEXT,
    record_version_id bigint REFERENCES record_version (id),
    parcel_id       TEXT,
    person_id       bigint,
    occurred_at     date,                              -- valid time, if the source says
    observed_at     timestamptz NOT NULL,                     -- system time
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_event_observed ON event (observed_at, id);
CREATE INDEX IF NOT EXISTS ix_event_kind ON event (kind, observed_at);
CREATE INDEX IF NOT EXISTS ix_event_parcel ON event (parcel_id);

-- ------------------------------------------------------------- entities -----
CREATE TABLE IF NOT EXISTS parcel (
    id              TEXT PRIMARY KEY,                  -- COUNTY/PIN_NORM
    county          TEXT NOT NULL,
    pin             TEXT NOT NULL,
    pin_norm        TEXT NOT NULL,
    situs_norm      TEXT,
    situs_components jsonb,
    zip             TEXT,
    lat             double precision,
    lon             double precision,
    land_use        TEXT,
    assessed_value  double precision,
    owner_string    TEXT,
    owner_mailing_norm TEXT,
    last_sale_date  date,
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    retired_at      timestamptz,
    superseded_by   TEXT,                              -- parcel.id after a renumber/merge
    UNIQUE (county, pin_norm)
);
CREATE INDEX IF NOT EXISTS ix_parcel_zip ON parcel (county, zip);

-- Parcels split, merge, and get renumbered. A join on PIN silently loses history
-- across a re-plat unless the lineage is explicit.
CREATE TABLE IF NOT EXISTS parcel_lineage (
    parent_id       TEXT NOT NULL,
    child_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,                     -- split | merge | renumber
    effective_date  date,
    source          TEXT,
    PRIMARY KEY (parent_id, child_id)
);

-- A person exists only once a link has been confirmed; unconfirmed candidates never
-- create an identity.
CREATE TABLE IF NOT EXISTS person (
    id              bigserial PRIMARY KEY,
    normalized_name TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    is_organization boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS person_alias (
    person_id       bigint NOT NULL REFERENCES person (id) ON DELETE CASCADE,
    alias_normalized TEXT NOT NULL,
    source          TEXT NOT NULL,
    PRIMARY KEY (person_id, alias_normalized, source)
);
CREATE TABLE IF NOT EXISTS person_address (
    person_id       bigint NOT NULL REFERENCES person (id) ON DELETE CASCADE,
    address_norm    TEXT NOT NULL,
    address_kind    TEXT NOT NULL,                     -- situs | tax_mailing | court_filing
    observed_at     timestamptz,
    PRIMARY KEY (person_id, address_norm, address_kind)
);

-- Every name occurrence in every source record. Resolution works on mentions, so
-- a new source only has to say which fields name people and in what role.
CREATE TABLE IF NOT EXISTS mention (
    id              bigserial PRIMARY KEY,
    source          TEXT NOT NULL,
    county          TEXT NOT NULL,
    natural_key     TEXT NOT NULL,
    record_version_id bigint NOT NULL REFERENCES record_version (id),
    role            TEXT NOT NULL,                     -- decedent | owner | grantor | grantee | personal_rep | ...
    position        INTEGER NOT NULL DEFAULT 0,
    raw_name        TEXT NOT NULL,
    parsed          jsonb NOT NULL,
    key_fl          TEXT NOT NULL,                     -- FIRST|LAST blocking key
    is_organization boolean NOT NULL DEFAULT false,
    address_norm    TEXT,
    parcel_id       TEXT,
    person_id       bigint REFERENCES person (id),
    current         boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS ix_mention_block ON mention (key_fl, current, source);
CREATE INDEX IF NOT EXISTS ix_mention_record ON mention (source, county, natural_key);

-- ------------------------------------------------------------- matching -----
CREATE TABLE IF NOT EXISTS match_run (
    id              bigserial PRIMARY KEY,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    tool_version    TEXT NOT NULL,
    rules_version   TEXT NOT NULL,
    model_version_id bigint,
    params          jsonb NOT NULL DEFAULT '{}'::jsonb,
    subjects        INTEGER NOT NULL DEFAULT 0,
    blocked_out     INTEGER NOT NULL DEFAULT 0,        -- subjects with no candidate at all
    candidates      INTEGER NOT NULL DEFAULT 0
);

-- One row per candidate identity assertion. features/probability/evidence/gates
-- are the rationale; status starts where the rules put it and is overwritten by a
-- human, whose name and time are kept.
CREATE TABLE IF NOT EXISTS entity_match (
    id              bigserial PRIMARY KEY,
    run_id          bigint REFERENCES match_run (id),
    kind            TEXT NOT NULL,                     -- e.g. estate_case->parcel
    left_source     TEXT NOT NULL,
    left_id         TEXT NOT NULL,
    right_source    TEXT NOT NULL,
    right_id        TEXT NOT NULL,
    left_mention_id bigint,
    right_mention_id bigint,
    person_id       bigint REFERENCES person (id),
    match_tier      TEXT NOT NULL,
    probability     double precision NOT NULL CHECK (probability >= 0 AND probability <= 1),
    features        jsonb NOT NULL DEFAULT '{}'::jsonb,        -- JSON feature -> value
    evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,        -- JSON list, human-readable labels
    flags           jsonb NOT NULL DEFAULT '[]'::jsonb,
    gates           jsonb NOT NULL DEFAULT '{}'::jsonb,        -- JSON gate -> passed
    status          TEXT NOT NULL DEFAULT 'pending',   -- confirmed | pending | rejected
    decided_by      TEXT NOT NULL DEFAULT 'rules',     -- rules | model | reviewer
    reviewer        TEXT,
    reviewed_at     timestamptz,
    review_note     TEXT,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    UNIQUE (left_source, left_id, right_source, right_id),
    CHECK (status <> 'confirmed' OR person_id IS NOT NULL OR reviewer IS NOT NULL OR decided_by <> 'reviewer')
);
CREATE INDEX IF NOT EXISTS ix_match_status ON entity_match (status, probability DESC);
CREATE INDEX IF NOT EXISTS ix_match_left ON entity_match (left_source, left_id);
CREATE INDEX IF NOT EXISTS ix_match_right ON entity_match (right_source, right_id);

-- Append-only reviewer decisions. The label table is what the model trains on.
CREATE TABLE IF NOT EXISTS review_decision (
    id              bigserial PRIMARY KEY,
    match_id        bigint NOT NULL REFERENCES entity_match (id),
    decision        TEXT NOT NULL,                     -- confirm | reject | skip
    reviewer        TEXT NOT NULL,
    decided_at      timestamptz NOT NULL,
    note            TEXT,
    seconds_spent   double precision
);

CREATE TABLE IF NOT EXISTS label (
    id              bigserial PRIMARY KEY,
    kind            TEXT NOT NULL,
    left_id         TEXT NOT NULL,
    right_id        TEXT NOT NULL,
    is_match        boolean NOT NULL,
    origin          TEXT NOT NULL,                     -- review | import | seed
    labeled_by      TEXT,
    labeled_at      timestamptz NOT NULL,
    note            TEXT,
    UNIQUE (kind, left_id, right_id)
);

CREATE TABLE IF NOT EXISTS model_version (
    id              bigserial PRIMARY KEY,
    kind            TEXT NOT NULL,
    trained_at      timestamptz NOT NULL,
    n_labels        INTEGER NOT NULL,
    weights         jsonb NOT NULL,
    intercept       double precision NOT NULL,
    metrics         jsonb NOT NULL DEFAULT '{}'::jsonb,
    active          boolean NOT NULL DEFAULT false
);

-- ------------------------------------------------------------- policy -------
CREATE TABLE IF NOT EXISTS suppression (
    id              bigserial PRIMARY KEY,
    kind            TEXT NOT NULL,                     -- person | address | parcel | county
    value_norm      TEXT NOT NULL,
    reason          TEXT NOT NULL,
    added_by        TEXT,
    added_at        timestamptz NOT NULL,
    expires_at      timestamptz,
    UNIQUE (kind, value_norm)
);

CREATE TABLE IF NOT EXISTS retention_policy (
    scope           TEXT PRIMARY KEY,                  -- source name, 'raw', 'event', ...
    ttl_days        INTEGER NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS access_log (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS export_log (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL,
    actor           TEXT NOT NULL,
    channel         TEXT NOT NULL,                     -- csv | webhook | digest | api
    watchlist_id    bigint,
    match_id        bigint,
    allowed         boolean NOT NULL,
    reasons         jsonb NOT NULL DEFAULT '[]'::jsonb,
    policy_version  TEXT NOT NULL
);

-- ------------------------------------------------------------- outputs ------
CREATE TABLE IF NOT EXISTS watchlist (
    id              bigserial PRIMARY KEY,
    name            TEXT NOT NULL,
    owner           TEXT NOT NULL,
    filters         jsonb NOT NULL DEFAULT '{}'::jsonb,        -- JSON
    channel         TEXT NOT NULL DEFAULT 'digest',    -- digest | webhook
    endpoint        TEXT,
    secret          TEXT,
    created_at      timestamptz NOT NULL,
    active          boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS notification (
    id              bigserial PRIMARY KEY,
    watchlist_id    bigint NOT NULL REFERENCES watchlist (id),
    event_id        bigint NOT NULL REFERENCES event (id),
    created_at      timestamptz NOT NULL,
    delivered_at    timestamptz,
    payload         jsonb NOT NULL,
    UNIQUE (watchlist_id, event_id)
);

CREATE TABLE IF NOT EXISTS quality_snapshot (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL,
    connector       TEXT NOT NULL,
    county          TEXT NOT NULL,
    freshness_hours double precision,
    rows_current    INTEGER,
    rows_delta      INTEGER,
    parse_fail_rate double precision,
    blocked_out_rate double precision,
    rolling_precision double precision,
    anomalies       jsonb NOT NULL DEFAULT '[]'::jsonb
);

-- Geometry: with PostGIS installed, add a real point and use it for boundary
-- queries; the lat/lon columns stay for engines without it.
--   CREATE EXTENSION IF NOT EXISTS postgis;
--   ALTER TABLE parcel ADD COLUMN geom geometry(Point, 4326);
--   CREATE INDEX ON parcel USING GIST (geom);

-- ------------------------------------------------------------- views --------
CREATE OR REPLACE VIEW v_review_queue AS
SELECT id, kind, left_id, right_id, match_tier, probability, evidence, flags, created_at
FROM entity_match
WHERE status = 'pending'
ORDER BY probability DESC, left_id, right_id;
