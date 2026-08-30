-- MonitorCLT probate: roles and row-level security.
--
-- This is the migration that makes Supabase worth using rather than a bare
-- Postgres box. The data is public record, but it is public record about named
-- private individuals -- decedents, their heirs, their addresses -- assembled
-- into exactly the kind of profile that is harmless one row at a time and not
-- harmless in bulk. So access is a schema object here, not a convention:
--
--   probate_loader    the pipeline. Writes sources, runs, and candidate matches.
--                     Cannot decide a match (no direct UPDATE of status).
--   probate_reviewer  a human clearing the queue. Reads the queue, calls
--                     record_review(). Cannot write a source table and cannot
--                     set a status by any route that skips the audit log.
--   probate_outreach  whoever contacts personal representatives. Sees the
--                     confirmed lead list and nothing else -- no pending
--                     candidates, no rejected ones, no review notes.
--
-- The outreach boundary is the important one. "A pending candidate is a
-- question, not a lead" is a comment in 0001; here it is a permission.

-- ---------------------------------------------------------------- roles -----
-- NOLOGIN group roles: grant them to real users (or, on Supabase, to the API
-- roles below) rather than logging in as them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probate_loader') THEN
        CREATE ROLE probate_loader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probate_reviewer') THEN
        CREATE ROLE probate_reviewer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probate_outreach') THEN
        CREATE ROLE probate_outreach NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA probate TO probate_loader, probate_reviewer, probate_outreach;

-- ------------------------------------------------------------- pipeline -----

GRANT SELECT, INSERT, UPDATE ON
    probate.estate_case, probate.parcel, probate.deed,
    probate.person, probate.person_alias, probate.person_address
TO probate_loader;
GRANT SELECT, INSERT ON probate.match_run TO probate_loader;
GRANT SELECT, INSERT, UPDATE ON probate.entity_match TO probate_loader;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA probate TO probate_loader;

-- ------------------------------------------------------------- reviewer -----
-- No table grants at all: the queue views are security-definer (see below), and
-- record_review() is the only write path. A reviewer therefore cannot confirm a
-- match without leaving an audit row, because there is no other door.

GRANT SELECT ON probate.v_review_queue, probate.v_review_queue_detail,
                probate.v_estate_property, probate.v_review_history
TO probate_reviewer;

-- PostgreSQL grants EXECUTE on a new function to PUBLIC. record_review() is
-- SECURITY DEFINER, so leaving that default in place would mean anyone who can
-- reach the database can confirm a match -- the pipeline included. Take it away
-- first, then hand it to the people whose job it is.
REVOKE ALL ON FUNCTION probate.record_review(bigint, probate.match_status, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION probate.record_review(bigint, probate.match_status, text)
TO probate_reviewer;

-- ------------------------------------------------------------- outreach -----

GRANT SELECT ON probate.v_estate_property TO probate_outreach;

-- ------------------------------------------------------------------ RLS -----
-- Every base table denies by default. probate_loader's access is granted by the
-- policies below; the reviewer and outreach roles hold no table grants and so
-- reach the data only through the views, which is the point.

ALTER TABLE probate.estate_case    ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.parcel         ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.deed           ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.person         ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.person_alias   ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.person_address ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.match_run      ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.entity_match   ENABLE ROW LEVEL SECURITY;
ALTER TABLE probate.match_review   ENABLE ROW LEVEL SECURITY;

CREATE POLICY loader_all ON probate.estate_case    FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.parcel         FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.deed           FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.person         FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.person_alias   FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.person_address FOR ALL TO probate_loader USING (true) WITH CHECK (true);
CREATE POLICY loader_all ON probate.match_run      FOR ALL TO probate_loader USING (true) WITH CHECK (true);

-- The pipeline writes candidates and re-scores them on every run. It cannot
-- overturn a decision: that is the entity_match_preserve_human_review trigger in
-- 0002, which silently restores the human's verdict on any UPDATE that is not
-- record_review(). It is a trigger rather than a policy so that a re-run
-- refreshes the rationale instead of failing on rows a reviewer has signed.
CREATE POLICY loader_all ON probate.entity_match FOR ALL TO probate_loader USING (true) WITH CHECK (true);

-- Nobody updates or deletes a decision; the log is append-only by permission,
-- not by etiquette. record_review() is SECURITY DEFINER and so inserts on the
-- reviewer's behalf without the reviewer holding INSERT here.
CREATE POLICY loader_read_reviews ON probate.match_review FOR SELECT TO probate_loader USING (true);

-- --------------------------------------------------------------- views ------
-- Left as security-definer (PostgreSQL's default for views) on purpose: it is
-- what lets probate_outreach read the confirmed lead list while holding no
-- privilege on entity_match, so pending and rejected candidates are unreachable
-- rather than merely unselected. Supabase's linter flags definer views by
-- default -- this is the intended configuration, not an oversight. Do not add
-- `security_invoker = true` here without also granting the base-table access
-- that would then be required, which would defeat the boundary.

-- ------------------------------------------------------------- Supabase -----
-- On Supabase the PostgREST roles exist; wire them up so a JWT with the right
-- role reaches the right view. On bare PostgreSQL these roles are absent and
-- this block is a no-op -- grant the three roles above to your own users
-- instead.
--
-- `authenticated` gets the reviewer role: it is the logged-in human clearing the
-- queue. `anon` gets nothing, ever. `service_role` gets the loader role for the
-- pipeline. Add `probate` to the API's exposed schemas for any of this to be
-- reachable over PostgREST.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT probate_loader TO service_role;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT probate_reviewer TO authenticated;
        GRANT USAGE ON SCHEMA probate TO authenticated;
    END IF;
END
$$;
