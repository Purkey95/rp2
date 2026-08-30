-- Seller Opportunity Score output table. Applied on the MonitorCLT host.
-- Reads: signals (tools/monitorclt_sourcing/schema.sql) + parcels (enrichment).
-- Written by: tools/monitorclt_score/db.py write_leads_db().

CREATE TABLE IF NOT EXISTS leads (
    apn                       text PRIMARY KEY,
    owner                     text,
    situs_address             text,
    seller_opportunity_score  integer NOT NULL,
    confidence                integer NOT NULL DEFAULT 100,  -- 0-100: points-weighted signal reliability
    band                      text NOT NULL,            -- immediate|priority|mail_call|digital|skip
    dimensions_firing         integer NOT NULL DEFAULT 0,
    portfolio_size            integer NOT NULL DEFAULT 1,
    components                jsonb   NOT NULL DEFAULT '{}',  -- the five component scores
    signals                   jsonb   NOT NULL DEFAULT '[]',  -- contributing signals + evidence + dimension
    scored_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS leads_score_idx ON leads (seller_opportunity_score DESC);
CREATE INDEX IF NOT EXISTS leads_band_idx  ON leads (band);

-- Priority queue the CRM / daily digest reads.
CREATE OR REPLACE VIEW priority_leads AS
SELECT apn, owner, situs_address, seller_opportunity_score, band, dimensions_firing,
       portfolio_size, components, signals, scored_at
FROM leads
WHERE seller_opportunity_score >= 61
ORDER BY seller_opportunity_score DESC;

-- MonitorCLT metrics for the digest:
--   score.leads_priority_plus = (SELECT count(*) FROM leads WHERE seller_opportunity_score >= 61)
--   score.leads_immediate     = (SELECT count(*) FROM leads WHERE band = 'immediate')
-- Component leaderboards, e.g. top financial-distress:
--   SELECT apn, (components->>'financial_distress')::int AS fin FROM leads ORDER BY fin DESC LIMIT 50;
