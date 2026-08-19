-- Pipeline offer-sheet persistence. Extends the score module's `leads` table
-- (tools/monitorclt_score/schema.sql) with the pipeline's valuation + why-now +
-- market columns, then the pipeline upserts full offer-sheet rows into it.
-- Apply score/schema.sql first, then this. Written by: monitorclt_pipeline/offer_db.py.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS why_now          numeric;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS estimated_value  integer;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_low        integer;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_high       integer;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_market_high integer;   -- exit-caution-adjusted
ALTER TABLE leads ADD COLUMN IF NOT EXISTS market_stance    text;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS valuation        jsonb DEFAULT '{}';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS top_signals      jsonb DEFAULT '[]';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_ready_at   timestamptz;

-- The offer queue the CRM / dispo desk reads: priority leads that already have a
-- valuation and offer band, richest first.
CREATE OR REPLACE VIEW offer_queue AS
SELECT apn, owner, situs_address, seller_opportunity_score, band, confidence,
       why_now, estimated_value, offer_low, offer_high, offer_market_high,
       market_stance, top_signals, offer_ready_at
FROM leads
WHERE seller_opportunity_score >= 41 AND estimated_value IS NOT NULL
ORDER BY seller_opportunity_score DESC, estimated_value DESC;

-- MonitorCLT metrics for the digest:
--   pipeline.offer_ready      = (SELECT count(*) FROM leads WHERE estimated_value IS NOT NULL)
--   pipeline.priority_offers  = (SELECT count(*) FROM offer_queue)
