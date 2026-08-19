-- Owner Distress Score output table. Applied on the MonitorCLT host.
-- Reads: signals (from tools/monitorclt_sourcing/schema.sql) + parcels (enrichment).
-- Written by: tools/monitorclt_score/db.py write_leads_db().

CREATE TABLE IF NOT EXISTS leads (
    apn             text PRIMARY KEY,
    owner           text,
    situs_address   text,
    score           integer NOT NULL,
    band            text NOT NULL,               -- immediate|priority|mail_call|digital|skip
    portfolio_size  integer NOT NULL DEFAULT 1,
    categories      text[]  NOT NULL DEFAULT '{}',
    signals         jsonb   NOT NULL DEFAULT '[]',  -- contributing signals + evidence URLs
    scored_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS leads_score_idx ON leads (score DESC);
CREATE INDEX IF NOT EXISTS leads_band_idx  ON leads (band);

-- Priority queue the CRM / daily digest reads.
CREATE OR REPLACE VIEW priority_leads AS
SELECT apn, owner, situs_address, score, band, portfolio_size, categories, signals, scored_at
FROM leads
WHERE score >= 61
ORDER BY score DESC;

-- MonitorCLT metrics: emit these into the digest.
--   score.leads_priority_plus = (SELECT count(*) FROM leads WHERE score >= 61)
--   score.leads_immediate     = (SELECT count(*) FROM leads WHERE band = 'immediate')
