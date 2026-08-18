-- MonitorCLT signals table — provenance-enforced storage for sourced evidence.
-- Adapted from HomeSignal's anti-fabrication model: a row cannot exist without a
-- source_url, and per-source coverage is graded pass / coverage_coming, never faked.

CREATE TABLE IF NOT EXISTS signals (
    signal_id      text PRIMARY KEY,               -- stable hash (idempotent upsert)
    source_name    text NOT NULL,
    signal_type    text NOT NULL,                  -- from type_map, else 'unclassified'
    status         text NOT NULL,
    bucket         text NOT NULL CHECK (bucket IN ('active','resolved','informational')),

    -- PROVENANCE (the whole point). A signal with no source_url must never be written.
    source_url     text NOT NULL CHECK (source_url <> ''),
    url_precision  text NOT NULL CHECK (url_precision IN ('record','dataset')),
    confidence     text NOT NULL CHECK (confidence IN ('verified','sourced')),
    retrieved_at   timestamptz NOT NULL,

    -- Subject
    apn            text,
    situs_address  text,
    situs_zip      text,
    lat            double precision,
    lng            double precision,
    geo_precision  text NOT NULL DEFAULT 'jurisdiction'
                     CHECK (geo_precision IN ('point','address','jurisdiction')),
    native_id      text,
    raw            jsonb,

    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_verified  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signals_apn_idx        ON signals (apn);
CREATE INDEX IF NOT EXISTS signals_zip_idx        ON signals (situs_zip);
CREATE INDEX IF NOT EXISTS signals_type_idx       ON signals (signal_type);
CREATE INDEX IF NOT EXISTS signals_source_idx     ON signals (source_name);

-- Idempotent upsert: re-running a source updates last_verified/status, never dups.
-- (Call once per emitted signal, or COPY into a temp table and upsert in bulk.)
--
--   INSERT INTO signals (...) VALUES (...)
--   ON CONFLICT (signal_id) DO UPDATE
--     SET status = EXCLUDED.status,
--         bucket = EXCLUDED.bucket,
--         confidence = EXCLUDED.confidence,
--         last_verified = now();

-- Per-source coverage grade — the data_quality gate as a query. A source that
-- produced >=1 sourced signal is 'pass'; otherwise 'coverage_coming'. Emit these
-- into MonitorCLT so coverage shows up in the daily digest.
CREATE OR REPLACE VIEW source_coverage AS
SELECT source_name,
       count(*)                                              AS signals,
       count(*) FILTER (WHERE confidence = 'verified')       AS verified,
       count(DISTINCT apn) FILTER (WHERE apn IS NOT NULL)    AS parcels,
       CASE WHEN count(*) >= 1 THEN 'pass' ELSE 'coverage_coming' END AS data_quality,
       max(last_verified)                                    AS last_run
FROM signals
GROUP BY source_name;
