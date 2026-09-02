#!/usr/bin/env python3
"""Tests for the run -> SQL loader.

Structural assertions on the generated SQL always run. If psql and
MONITORCLT_TEST_DSN are available the SQL is also executed against a scratch
schema; otherwise that test is skipped, not failed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402
import load_run  # noqa: E402

SAMPLE = os.path.join(HERE, "sample")


def sample_run():
    estates = crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl"))
    parcels = crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl"))
    deeds = crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl"))
    entities = crossref.load_records(os.path.join(SAMPLE, "business_entities.jsonl"))
    result = crossref.crossref(estates, parcels, deeds, crossref.load_rules(), entities=entities)
    return result, estates, parcels, deeds, entities


class TestLiterals(unittest.TestCase):
    def test_quotes_are_doubled_never_interpolated(self):
        self.assertEqual(load_run.sql_literal("O'Brien"), "'O''Brien'")

    def test_backslash_uses_escape_string_syntax(self):
        self.assertEqual(load_run.sql_literal("a\\b"), "E'a\\\\b'")

    def test_null_bool_number_jsonb(self):
        self.assertEqual(load_run.sql_literal(None), "NULL")
        self.assertEqual(load_run.sql_literal(True), "TRUE")
        self.assertEqual(load_run.sql_literal(312500), "312500")
        self.assertEqual(load_run.sql_literal(["a", "b"], jsonb=True), "'[\"a\", \"b\"]'::jsonb")

    def test_injection_attempt_in_a_name_stays_a_string(self):
        sql = load_run.render(
            {"run": {"tool_version": "1.1", "rules_version": "1.1"}, "matches": []},
            estates=[{"county": "X", "file_number": "1", "decedent_name": "x'); DROP TABLE probate.parcel; --"}],
        )
        self.assertIn("'x''); DROP TABLE probate.parcel; --'", sql)
        self.assertNotIn("\nDROP TABLE", sql)


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, cls.estates, cls.parcels, cls.deeds, cls.entities = sample_run()
        cls.sql = load_run.render(
            cls.result, cls.estates, cls.parcels, cls.deeds, cls.entities, params={"inputs": {"x": 1}}
        )

    def test_one_transaction(self):
        self.assertTrue(self.sql.startswith("-- generated"))
        self.assertEqual(self.sql.count("\nBEGIN;"), 1)
        self.assertTrue(self.sql.rstrip().endswith("COMMIT;"))

    def test_every_match_row_is_upserted_once(self):
        self.assertEqual(
            self.sql.count("INSERT INTO probate.entity_match"), len(self.result["matches"])
        )
        rejected = [l for l in self.result["matches"] if l["status"] == "rejected"]
        self.assertTrue(rejected)  # rejected rows load too
        self.assertIn("'rejected'", self.sql)

    def test_confirmed_rows_carry_the_rule_run_as_reviewer_and_nothing_else_does(self):
        confirmed = sum(1 for l in self.result["matches"] if l["status"] == "confirmed")
        self.assertEqual(self.sql.count("'rules@' || '1.1' || '/run:'"), confirmed)
        self.assertNotIn("person_id", self.sql.split("-- candidate links")[1].split("-- where")[0])

    def test_human_decisions_are_preserved_on_conflict(self):
        self.assertIn(
            "status = CASE WHEN probate.entity_match.reviewed_at IS NULL THEN EXCLUDED.status "
            "ELSE probate.entity_match.status END",
            self.sql,
        )
        self.assertNotIn("reviewed_at = EXCLUDED", self.sql)
        self.assertNotIn("review_note = EXCLUDED", self.sql)
        self.assertIn("evidence = EXCLUDED.evidence", self.sql)

    def test_via_columns_and_entities_are_loaded(self):
        self.assertIn("'business_entity', '1234567'", self.sql)
        self.assertEqual(self.sql.count("INSERT INTO probate.business_entity"), 3)
        self.assertEqual(self.sql.count("INSERT INTO probate.entity_official"), 4)

    def test_sources_upsert_on_natural_keys(self):
        self.assertIn("ON CONFLICT (county, file_number) DO UPDATE", self.sql)
        self.assertIn("ON CONFLICT (county, pin) DO UPDATE", self.sql)
        self.assertIn("ON CONFLICT (county, book, page, instrument_number) DO UPDATE", self.sql)
        self.assertIn("ON CONFLICT (sos_id) DO UPDATE", self.sql)

    def test_missing_retrieved_at_uses_the_column_default(self):
        self.assertIn(", DEFAULT)\n  ON CONFLICT (county, pin)", self.sql)

    def test_run_provenance_is_recorded(self):
        self.assertIn("INSERT INTO probate.match_run (tool_version, rules_version, params)", self.sql)
        self.assertIn('"inputs": {"x": 1}', self.sql)
        self.assertIn('"collapsed_duplicates": 0', self.sql)

    def test_disagreement_report_closes_the_load(self):
        self.assertIn("WHERE m.reviewed_at IS NOT NULL AND m.status::text <> i.status", self.sql)

    def test_output_is_deterministic(self):
        again = load_run.render(
            self.result, self.estates, self.parcels, self.deeds, self.entities, params={"inputs": {"x": 1}}
        )
        self.assertEqual(again, self.sql)

    def test_cli_writes_the_same_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_path = os.path.join(tmp, "run.json")
            with open(run_path, "w", encoding="utf-8") as f:
                json.dump(self.result, f)
            out_path = os.path.join(tmp, "load.sql")
            load_run.main(["--run", run_path, "--parcels", os.path.join(SAMPLE, "parcels.jsonl"), "--out", out_path])
            with open(out_path, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("INSERT INTO probate.parcel", text)
        self.assertNotIn("INSERT INTO probate.estate_case", text)


@unittest.skipUnless(
    shutil.which("psql") and os.environ.get("MONITORCLT_TEST_DSN"),
    "needs psql and MONITORCLT_TEST_DSN",
)
class TestAgainstPostgres(unittest.TestCase):
    def test_load_twice_then_review_then_reload(self):
        dsn = os.environ["MONITORCLT_TEST_DSN"]
        result, estates, parcels, deeds, entities = sample_run()
        sql = load_run.render(result, estates, parcels, deeds, entities)

        def psql(text):
            return subprocess.run(
                ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-1", "-At", "-c", text],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as f:
            psql("DROP SCHEMA IF EXISTS probate CASCADE; " + f.read())
        psql(sql)
        psql(sql)  # idempotent
        self.assertEqual(psql("SELECT count(*) FROM probate.entity_match"), str(len(result["matches"])))
        self.assertEqual(psql("SELECT count(*) FROM probate.match_run"), "2")
        psql(
            "UPDATE probate.entity_match SET status = 'rejected', reviewer = 'tester', "
            "reviewed_at = now() WHERE right_id = 'MECKLENBURG/045-133-19'"
        )
        psql(sql)
        self.assertEqual(
            psql("SELECT status FROM probate.entity_match WHERE right_id = 'MECKLENBURG/045-133-19'"),
            "rejected",
        )
        self.assertEqual(
            psql("SELECT count(*) FROM probate.v_review_queue WHERE via_id = '1234567'"), "1"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
