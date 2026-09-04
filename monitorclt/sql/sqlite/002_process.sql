-- MonitorCLT process additions: wider blocking, geocoding, outreach outcomes.

-- Every key a mention can be found under (exact, nickname, initial, phonetic).
CREATE TABLE IF NOT EXISTS mention_block (
    mention_id      INTEGER NOT NULL REFERENCES mention (id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    PRIMARY KEY (mention_id, key)
);
CREATE INDEX IF NOT EXISTS ix_mention_block_key ON mention_block (key);

-- Geocoding results, cached forever by normalized address so a provider is asked once.
CREATE TABLE IF NOT EXISTS geocode_cache (
    address_norm    TEXT PRIMARY KEY,
    lat             REAL,
    lon             REAL,
    provider        TEXT NOT NULL,
    geocoded_at     TEXT NOT NULL
);

-- What happened after a lead left the system. Closes the loop from outreach back to
-- signals and suppression.
CREATE TABLE IF NOT EXISTS outcome (
    id              INTEGER PRIMARY KEY,
    match_id        INTEGER NOT NULL REFERENCES entity_match (id),
    lead_id         TEXT NOT NULL,
    outcome         TEXT NOT NULL,      -- reached | no_response | declined | not_in_estate | already_sold | under_contract | closed | invalid_contact
    recorded_by     TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS ix_outcome_match ON outcome (match_id);
