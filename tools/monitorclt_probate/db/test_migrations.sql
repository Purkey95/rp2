-- Behavioural tests for the probate migrations.
--
-- The interesting claims in 0002 and 0003 are not about columns, they are about
-- what the database refuses to do: a re-run cannot overturn a human, a decision
-- cannot be made without an audit row, and outreach cannot see a candidate that
-- is still a question. Those are worth testing, because each one is a rule the
-- README states and a reader would otherwise have to take on trust.
--
--   createdb probate_test
--   ./apply.sh "postgresql:///probate_test"
--   psql "postgresql:///probate_test" -v ON_ERROR_STOP=1 -f test_migrations.sql
--
-- Runs in one transaction and rolls back, so the database is unchanged. Prints
-- one line per test; any failure aborts with the assertion that failed.

BEGIN;

CREATE OR REPLACE FUNCTION pg_temp.ok(condition boolean, what text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF NOT condition THEN
        RAISE EXCEPTION 'FAIL: %', what;
    END IF;
    RAISE NOTICE 'ok  %', what;
END;
$$;

-- ------------------------------------------------------------- fixtures ----

INSERT INTO probate.estate_case (county, file_number, decedent_name, date_of_death, personal_rep_name)
VALUES ('MECKLENBURG', '26 E 0001', 'JOHN Q PUBLIC', '2026-01-15', 'JANE PUBLIC');

INSERT INTO probate.parcel (county, pin, owner_name, situs_address, assessed_value)
VALUES ('MECKLENBURG', '123-456-78', 'PUBLIC JOHN Q', '1 MAIN ST', 250000.00);

INSERT INTO probate.match_run (tool_version, rules_version) VALUES ('1.0', '1.0');

INSERT INTO probate.entity_match (
    run_id, left_source, left_id, right_source, right_id,
    match_tier, score, evidence, flags, status)
VALUES (
    (SELECT max(id) FROM probate.match_run),
    'estate_case', 'MECKLENBURG/26 E 0001',
    'parcel', 'MECKLENBURG/123-456-78',
    'name_full_exact', 0.700, '["name_full_exact"]'::jsonb, '[]'::jsonb, 'pending');

-- ------------------------------------------------------------ the queue ----

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_review_queue_detail) = 1,
    'a pending match appears in the review queue');

SELECT pg_temp.ok(
    (SELECT decedent_name || '|' || owner_name FROM probate.v_review_queue_detail)
        = 'JOHN Q PUBLIC|PUBLIC JOHN Q',
    'the queue inlines both sides of the assertion');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_estate_property) = 0,
    'a pending match is NOT on the lead list');

-- ------------------------------------------------------ recording review ---

SELECT probate.record_review(
    (SELECT id FROM probate.entity_match), 'confirmed', 'verified deed + situs');

SELECT pg_temp.ok(
    (SELECT status FROM probate.entity_match) = 'confirmed',
    'record_review moves the match to confirmed');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.match_review) = 1,
    'record_review writes exactly one audit row');

SELECT pg_temp.ok(
    (SELECT previous_status = 'pending' AND decision = 'confirmed'
       AND reviewer IS NOT NULL AND score_at_review = 0.700
       AND rules_version = '1.0'
       FROM probate.match_review),
    'the audit row records the transition, the reviewer, and what they saw');

-- record_review() is SECURITY DEFINER, where current_user is the function's
-- owner rather than the caller. If current_reviewer() used it, every decision
-- would be signed with one name and the log would attribute nothing.
SELECT pg_temp.ok(
    (SELECT reviewer FROM probate.match_review) = session_user,
    'the decision is attributed to the role that connected, not the function owner');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_estate_property) = 1,
    'a confirmed match reaches the lead list');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_review_queue_detail) = 0,
    'and leaves the review queue');

-- -------------------------------------------------- re-runs vs decisions ---
-- The load_run.py upsert, in miniature: same conflict target, refreshed
-- rationale, matcher-decided status. The verdict must survive it.

INSERT INTO probate.entity_match (
    run_id, left_source, left_id, right_source, right_id,
    match_tier, score, evidence, flags, status)
VALUES (
    (SELECT max(id) FROM probate.match_run),
    'estate_case', 'MECKLENBURG/26 E 0001',
    'parcel', 'MECKLENBURG/123-456-78',
    'name_first_last_only', 0.550, '["name_first_last_only"]'::jsonb, '[]'::jsonb, 'pending')
ON CONFLICT (left_source, left_id, right_source, right_id) DO UPDATE SET
    match_tier = EXCLUDED.match_tier,
    score      = EXCLUDED.score,
    evidence   = EXCLUDED.evidence,
    flags      = EXCLUDED.flags,
    status     = EXCLUDED.status;

SELECT pg_temp.ok(
    (SELECT status FROM probate.entity_match) = 'confirmed',
    're-running the matcher does NOT overturn a human decision');

SELECT pg_temp.ok(
    (SELECT score FROM probate.entity_match) = 0.550
    AND (SELECT match_tier FROM probate.entity_match) = 'name_first_last_only',
    're-running the matcher DOES refresh the rationale');

SELECT pg_temp.ok(
    (SELECT reviewer IS NOT NULL AND reviewed_at IS NOT NULL FROM probate.entity_match),
    'and leaves the signature intact');

-- A bare UPDATE is not a back door either.
UPDATE probate.entity_match SET status = 'rejected', reviewer = NULL;

SELECT pg_temp.ok(
    (SELECT status FROM probate.entity_match) = 'confirmed',
    'a bare UPDATE cannot launder a status change past the audit log');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.match_review) = 1,
    'and wrote no audit row, because it changed nothing');

-- Reversal is a new row, not an edit.
SELECT probate.record_review(
    (SELECT id FROM probate.entity_match), 'pending', 'heir disputes the situs match');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.match_review) = 2,
    'a reversal appends a second decision rather than editing the first');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_review_history WHERE match_id IS NOT NULL) = 2,
    'v_review_history shows the whole sequence');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.v_estate_property) = 0,
    'and the lead comes straight back off the lead list');

-- ------------------------------------------------------------- outreach ----
-- The boundary that matters: whoever contacts personal representatives can read
-- confirmed leads and cannot reach a candidate that is still a question.

SELECT pg_temp.ok(
    has_table_privilege('probate_outreach', 'probate.v_estate_property', 'SELECT'),
    'outreach can read the confirmed lead list');

SELECT pg_temp.ok(
    NOT has_table_privilege('probate_outreach', 'probate.entity_match', 'SELECT'),
    'outreach cannot read entity_match');

SELECT pg_temp.ok(
    NOT has_table_privilege('probate_outreach', 'probate.v_review_queue', 'SELECT')
    AND NOT has_table_privilege('probate_outreach', 'probate.v_review_queue_detail', 'SELECT'),
    'outreach cannot read the review queue');

SELECT pg_temp.ok(
    NOT has_table_privilege('probate_outreach', 'probate.estate_case', 'SELECT')
    AND NOT has_table_privilege('probate_outreach', 'probate.parcel', 'SELECT'),
    'outreach cannot read the source tables directly');

-- ------------------------------------------------------------- reviewer ----

SELECT pg_temp.ok(
    has_table_privilege('probate_reviewer', 'probate.v_review_queue_detail', 'SELECT'),
    'a reviewer can read the queue');

SELECT pg_temp.ok(
    NOT has_table_privilege('probate_reviewer', 'probate.entity_match', 'UPDATE'),
    'a reviewer cannot UPDATE a match directly -- record_review() is the only door');

SELECT pg_temp.ok(
    has_function_privilege('probate_reviewer',
        'probate.record_review(bigint, probate.match_status, text)', 'EXECUTE'),
    'and can walk through it');

SELECT pg_temp.ok(
    NOT has_table_privilege('probate_reviewer', 'probate.match_review', 'INSERT')
    AND NOT has_table_privilege('probate_reviewer', 'probate.match_review', 'UPDATE')
    AND NOT has_table_privilege('probate_reviewer', 'probate.match_review', 'DELETE'),
    'nobody can hand-write, edit or delete an audit row');

-- --------------------------------------------------------------- loader ----

SELECT pg_temp.ok(
    has_table_privilege('probate_loader', 'probate.entity_match', 'INSERT')
    AND has_table_privilege('probate_loader', 'probate.parcel', 'INSERT'),
    'the pipeline can write candidates and sources');

SELECT pg_temp.ok(
    NOT has_function_privilege('probate_loader',
        'probate.record_review(bigint, probate.match_status, text)', 'EXECUTE'),
    'the pipeline cannot decide a match');

SELECT pg_temp.ok(
    (SELECT bool_and(relrowsecurity) FROM pg_class
      WHERE relnamespace = 'probate'::regnamespace AND relkind = 'r'),
    'row-level security is on for every base table');

-- ----------------------------------------------------- schema guarantees ---
-- The absence in the README is a schema property, so assert it like one.

SELECT pg_temp.ok(
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'probate'
        AND (column_name ~* 'incarcerat|arrest|probation|parole|inmate|offender|conviction')) = 0,
    'no criminal-justice column exists anywhere in the schema');

SELECT pg_temp.ok(
    (SELECT count(*) FROM information_schema.tables
      WHERE table_schema = 'probate'
        AND (table_name ~* 'incarcerat|arrest|probation|parole|inmate|offender')) = 0,
    'and no table for one to land in');

-- ----------------------------------------------------- person identity -----

INSERT INTO probate.person (source_ref, normalized_name, display_name)
VALUES ('estate_case:MECKLENBURG/26 E 0001', 'JOHN Q PUBLIC', 'JOHN Q PUBLIC');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.person) = 1,
    'a person can be minted from an estate case');

DO $$
BEGIN
    BEGIN
        INSERT INTO probate.person (source_ref, normalized_name, display_name)
        VALUES ('estate_case:MECKLENBURG/26 E 0001', 'JOHN QUINCY PUBLIC', 'JOHN QUINCY PUBLIC');
        RAISE EXCEPTION 'FAIL: a second identity was minted for one estate case';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'ok  one estate case mints at most one identity';
    END;
END
$$;

-- Two different people may share a name; that must remain possible.
INSERT INTO probate.person (source_ref, normalized_name, display_name)
VALUES ('estate_case:MECKLENBURG/26 E 0002', 'JOHN Q PUBLIC', 'JOHN Q PUBLIC');

SELECT pg_temp.ok(
    (SELECT count(*) FROM probate.person WHERE normalized_name = 'JOHN Q PUBLIC') = 2,
    'two decedents may share a name -- a name is not a key');

ROLLBACK;
