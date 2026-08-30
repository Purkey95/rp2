-- MonitorCLT probate: the human review workflow.
--
-- 0001 gives entity_match a status and a reviewer column. That is enough to know
-- what a match is *now*, but not how it got there -- and "reviewable and
-- reversible" is a claim you can only keep if every decision is recorded, not
-- just the latest one. This migration adds the append-only decision log, the one
-- function that is allowed to change a status, and the queue view a reviewer
-- actually works from (the candidate plus the records it is asserting are the
-- same person, so nobody has to go look them up in another tab).
--
-- Applies to Supabase (hosted or self-hosted) and to bare PostgreSQL alike:
-- nothing here depends on the Supabase `auth` schema.

-- ------------------------------------------------------- person identity ----
-- 0001 says a canonical person exists only once a match has been confirmed, but
-- gives nothing to key that person on, and a name is not a key: two Mecklenburg
-- decedents can share one. The anchor is the estate case -- "the decedent of
-- file 24 E 1234" is unique by construction -- so record which record the person
-- was minted from and let that be the identity.

ALTER TABLE probate.person
    ADD COLUMN source_ref text UNIQUE;

COMMENT ON COLUMN probate.person.source_ref IS
    'The source record this identity was minted from, e.g. '
    '"estate_case:MECKLENBURG/24 E 1234". Never a name: names are not keys.';

-- ------------------------------------------------------------ reviewer id ---
-- Under PostgREST/Supabase the reviewer is the JWT's email claim; under psql it
-- is the database user. Same function either way, so the audit rows say who did
-- it regardless of how they connected.

CREATE OR REPLACE FUNCTION probate.current_reviewer()
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce(
        nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'email', ''),
        nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', ''),
        current_user
    );
$$;

-- --------------------------------------------------------- decision log -----
-- Append-only: no UPDATE or DELETE grant is ever issued on this table (see
-- 0003_rls.sql). A reversal is a new row with the opposite decision, which is
-- why `previous_status` is recorded -- the history reads as a sequence of
-- transitions rather than a set of unordered opinions.

CREATE TABLE probate.match_review (
    id                  bigserial PRIMARY KEY,
    match_id            bigint      NOT NULL REFERENCES probate.entity_match (id) ON DELETE CASCADE,
    previous_status     probate.match_status NOT NULL,
    decision            probate.match_status NOT NULL,
    reviewer            text        NOT NULL DEFAULT probate.current_reviewer(),
    note                text,
    -- what the reviewer was looking at when they decided: the score and evidence
    -- can change on a later run, and a decision must stay interpretable against
    -- the rules that were in force when it was made.
    score_at_review     numeric(4, 3),
    evidence_at_review  jsonb,
    rules_version       text,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (decision <> previous_status OR note IS NOT NULL)
);

CREATE INDEX ON probate.match_review (match_id, decided_at DESC);
CREATE INDEX ON probate.match_review (reviewer, decided_at DESC);

COMMENT ON TABLE probate.match_review IS
    'Append-only log of human decisions on entity_match rows. Never updated or '
    'deleted; a reversal is a new row.';

-- ------------------------------------------------------------- the verb -----
-- The only supported way to move a match between statuses. Doing it through a
-- function rather than a bare UPDATE means the audit row cannot be forgotten,
-- and it is the single place where "a human confirmed this" gets stamped onto
-- the match itself.

CREATE OR REPLACE FUNCTION probate.record_review(
    p_match_id  bigint,
    p_decision  probate.match_status,
    p_note      text DEFAULT NULL
)
RETURNS probate.entity_match
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = probate, pg_catalog
AS $$
DECLARE
    v_match     probate.entity_match;
    v_reviewer  text := probate.current_reviewer();
    v_rules     text;
BEGIN
    SELECT * INTO v_match FROM probate.entity_match WHERE id = p_match_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no entity_match with id %', p_match_id;
    END IF;

    SELECT r.rules_version INTO v_rules FROM probate.match_run r WHERE r.id = v_match.run_id;

    INSERT INTO probate.match_review (
        match_id, previous_status, decision, reviewer, note,
        score_at_review, evidence_at_review, rules_version
    )
    VALUES (
        p_match_id, v_match.status, p_decision, v_reviewer, p_note,
        v_match.score, v_match.evidence, v_rules
    );

    -- Lift the guard below for this statement only: this is the one code path
    -- that is allowed to overwrite a decision, because it has just logged it.
    PERFORM set_config('probate.recording_review', 'on', true);

    UPDATE probate.entity_match
       SET status      = p_decision,
           reviewer    = v_reviewer,
           reviewed_at = now(),
           review_note = coalesce(p_note, review_note)
     WHERE id = p_match_id
    RETURNING * INTO v_match;

    PERFORM set_config('probate.recording_review', 'off', true);
    RETURN v_match;
END;
$$;

COMMENT ON FUNCTION probate.record_review IS
    'Record a human decision on a match: writes the audit row and updates the '
    'match in one transaction. The only supported way to change entity_match.status.';

-- ------------------------------------------------- re-runs vs. decisions ----
-- The matcher is expected to run again next week, over the same estates, with
-- possibly different weights. When it does, a candidate a human already decided
-- must keep that decision: the rationale (score, tier, evidence, flags) is
-- refreshed, because it should reflect the current rules, but the verdict is
-- the human's and the pipeline does not get a vote.
--
-- Enforced here rather than only in the loader, so it holds for every client
-- that ever writes to this table.

CREATE OR REPLACE FUNCTION probate.preserve_human_review()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.reviewer IS NOT NULL
       AND coalesce(current_setting('probate.recording_review', true), 'off') <> 'on' THEN
        NEW.status      := OLD.status;
        NEW.reviewer    := OLD.reviewer;
        NEW.reviewed_at := OLD.reviewed_at;
        NEW.review_note := OLD.review_note;
        NEW.person_id   := OLD.person_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER entity_match_preserve_human_review
    BEFORE UPDATE ON probate.entity_match
    FOR EACH ROW EXECUTE FUNCTION probate.preserve_human_review();

-- ------------------------------------------------------------ the queue -----
-- v_review_queue in 0001 answers "what is outstanding". This answers "what am I
-- looking at", which is what a reviewer needs: the candidate, both sides of the
-- assertion, and the deed instruments that were cited as corroboration.

CREATE VIEW probate.v_review_queue_detail AS
SELECT m.id            AS match_id,
       m.score,
       m.match_tier,
       m.evidence,
       m.flags,
       m.created_at,
       r.rules_version,
       e.county,
       e.file_number,
       e.decedent_name,
       e.date_of_death,
       e.filing_date,
       e.personal_rep_name,
       e.pr_mailing_address,
       e.source_url    AS estate_source_url,
       p.pin,
       p.owner_name,
       p.situs_address,
       p.owner_mailing_address,
       p.land_use,
       p.assessed_value,
       p.source_url    AS parcel_source_url,
       (SELECT count(*) FROM probate.deed d
         WHERE d.county = p.county AND d.parcel_pin = p.pin) AS deeds_on_pin
FROM probate.entity_match m
LEFT JOIN probate.match_run r ON r.id = m.run_id
JOIN probate.estate_case e
  ON m.left_source = 'estate_case'
 AND e.county || '/' || e.file_number = m.left_id
JOIN probate.parcel p
  ON m.right_source = 'parcel'
 AND p.county || '/' || p.pin = m.right_id
WHERE m.status = 'pending'
ORDER BY m.score DESC, e.file_number, p.pin;

COMMENT ON VIEW probate.v_review_queue_detail IS
    'The review queue with both sides of each assertion inlined, so a decision '
    'can be made without leaving the row.';

-- Who decided what, most recent first. The answer to "why is this a lead?".
CREATE VIEW probate.v_review_history AS
SELECT rv.match_id,
       m.left_id,
       m.right_id,
       rv.previous_status,
       rv.decision,
       rv.reviewer,
       rv.note,
       rv.score_at_review,
       rv.rules_version,
       rv.decided_at
FROM probate.match_review rv
JOIN probate.entity_match m ON m.id = rv.match_id
ORDER BY rv.decided_at DESC;
