#!/usr/bin/env python3
"""Tests for load_run.py. No database: the plan is pure, so it can be read."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crossref  # noqa: E402
import load_run  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample")


def sample_result():
    rules = crossref.load_rules(None)
    return crossref.crossref(
        crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
        rules,
    )


def steps_for(label, steps):
    return [step for step in steps if step[0] == label]


class FakeCursor:
    """Enough of DB-API to record what a load would send, and hand back ids."""

    def __init__(self, log):
        self.log = log
        self._next_id = 0
        self._last = None

    def execute(self, sql, params):
        self.log.append((sql, params))
        self._next_id += 1
        self._last = (self._next_id,)

    def fetchone(self):
        return self._last

    def close(self):
        pass


class FakeConnection:
    def __init__(self, fail_on=None):
        self.log = []
        self.committed = False
        self.rolled_back = False
        self.fail_on = fail_on

    def cursor(self):
        connection = self

        class Cursor(FakeCursor):
            def execute(self, sql, params):
                if connection.fail_on is not None and len(connection.log) == connection.fail_on:
                    raise RuntimeError("boom")
                FakeCursor.execute(self, sql, params)

        return Cursor(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.result = sample_result()

    def test_every_match_becomes_one_entity_match_statement(self):
        steps = load_run.plan(self.result)
        self.assertEqual(len(steps_for("entity_match", steps)), len(self.result["matches"]))

    def test_run_is_opened_before_any_match_is_written(self):
        steps = load_run.plan(self.result)
        labels = [step[0] for step in steps]
        self.assertLess(labels.index("match_run"), labels.index("entity_match"))

    def test_sources_are_upserted_when_given(self):
        sources = {
            "estates": crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
            "parcels": crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
            "deeds": crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
        }
        steps = load_run.plan(self.result, sources)
        self.assertEqual(len(steps_for("estate_case", steps)), len(sources["estates"]))
        self.assertEqual(len(steps_for("parcel", steps)), len(sources["parcels"]))
        self.assertEqual(len(steps_for("deed", steps)), len(sources["deeds"]))
        # and before the run, so the views resolve the moment the matches land
        labels = [step[0] for step in steps]
        self.assertLess(labels.index("parcel"), labels.index("match_run"))

    def test_confirmed_matches_mint_a_person_pending_ones_do_not(self):
        steps = load_run.plan(self.result)
        confirmed = {m["left_id"] for m in self.result["matches"] if m["status"] == "confirmed"}
        self.assertTrue(confirmed, "sample should contain at least one confirmed match")
        minted = {step[1][1][0] for step in steps_for("person", steps)}
        self.assertEqual(minted, {"estate_case:{0}".format(left) for left in confirmed})

    def test_person_is_keyed_on_the_estate_case_never_on_a_name(self):
        steps = load_run.plan(self.result)
        for _label, (sql, params), _returns in steps_for("person", steps):
            self.assertIn("ON CONFLICT (source_ref)", sql)
            self.assertTrue(params[0].startswith("estate_case:"))

    def test_one_person_per_estate_even_with_several_confirmed_parcels(self):
        result = sample_result()
        confirmed = [m for m in result["matches"] if m["status"] == "confirmed"]
        # a second confirmed parcel on the same estate must not mint a second identity
        twin = dict(confirmed[0])
        twin["right_id"] = confirmed[0]["right_id"] + "-B"
        result["matches"] = result["matches"] + [twin]
        steps = load_run.plan(result)
        keys = [step[1][1][0] for step in steps_for("person", steps)]
        self.assertEqual(len(keys), len(set(keys)))

    def test_confirmed_match_carries_its_person_reference(self):
        steps = load_run.plan(self.result)
        by_status = {}
        for _label, (_sql, params), _returns in steps_for("entity_match", steps):
            by_status.setdefault(params[-1], []).append(params[5])
        for person_id in by_status.get("confirmed", []):
            self.assertIsInstance(person_id, load_run.Ref)
        for status in ("pending", "rejected"):
            for person_id in by_status.get(status, []):
                self.assertIsNone(person_id, "only a confirmed match may name a person")

    def test_no_value_is_ever_interpolated_into_a_statement(self):
        steps = load_run.plan(self.result)
        for label, (sql, params), _returns in steps:
            self.assertEqual(
                sql.count("%s"),
                len(params),
                "{0}: placeholders and parameters must correspond".format(label),
            )
            for value in params:
                if isinstance(value, str):
                    self.assertNotIn(value, sql)

    def test_a_rerun_refreshes_the_rationale_but_not_the_verdict(self):
        # The loader writes status; the database trigger is what protects a
        # reviewed row. The upsert must therefore leave reviewer/reviewed_at
        # alone -- it has no business asserting who decided anything.
        steps = load_run.plan(self.result)
        sql = steps_for("entity_match", steps)[0][1][0]
        self.assertIn("ON CONFLICT (left_source, left_id, right_source, right_id) DO UPDATE", sql)
        for column in ("score", "evidence", "flags", "match_tier"):
            self.assertIn("{0} = EXCLUDED.{0}".format(column), sql)
        for column in ("reviewer", "reviewed_at", "review_note"):
            self.assertNotIn(column, sql)

    def test_person_id_is_never_downgraded_to_null_on_rerun(self):
        steps = load_run.plan(self.result)
        sql = steps_for("entity_match", steps)[0][1][0]
        self.assertIn("person_id = coalesce(probate.entity_match.person_id, EXCLUDED.person_id)", sql)

    def test_deed_key_is_total_so_a_rerun_does_not_duplicate(self):
        steps = load_run.plan(
            self.result, {"deeds": crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl"))}
        )
        sql = steps_for("deed", steps)[0][1][0]
        self.assertIn("coalesce(%s, '')", sql)
        self.assertIn("ON CONFLICT (county, book, page, instrument_number)", sql)

    def test_empty_dates_become_null_not_empty_string(self):
        _sql, params = load_run.upsert_estate_case(
            {"county": "MECKLENBURG", "file_number": "1", "decedent_name": "X", "date_of_death": ""}
        )
        self.assertIsNone(params[3])

    def test_timestamps_are_trimmed_to_a_date(self):
        _sql, params = load_run.upsert_estate_case(
            {"county": "M", "file_number": "1", "decedent_name": "X", "filing_date": "2026-01-02T00:00:00Z"}
        )
        self.assertEqual(params[4], "2026-01-02")


class TestExecute(unittest.TestCase):
    def setUp(self):
        self.result = sample_result()

    def test_returned_ids_are_threaded_into_later_statements(self):
        steps = load_run.plan(self.result)
        connection = FakeConnection()
        load_run.execute(connection, steps)
        self.assertTrue(connection.committed)
        for sql, params in connection.log:
            for value in params:
                self.assertNotIsInstance(value, load_run.Ref, "every Ref must be resolved: {0}".format(sql))

    def test_run_id_is_the_id_the_database_returned(self):
        steps = load_run.plan(self.result)
        connection = FakeConnection()
        load_run.execute(connection, steps)
        run_index = next(i for i, (sql, _p) in enumerate(connection.log) if "match_run" in sql)
        match_sql, match_params = next(
            (sql, p) for sql, p in connection.log if "INSERT INTO probate.entity_match" in sql
        )
        self.assertEqual(match_params[0], run_index + 1)
        self.assertIn("entity_match", match_sql)

    def test_a_failure_rolls_the_whole_load_back(self):
        steps = load_run.plan(self.result)
        connection = FakeConnection(fail_on=3)
        with self.assertRaises(RuntimeError):
            load_run.execute(connection, steps)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_counts_are_reported_per_table(self):
        steps = load_run.plan(self.result)
        written = load_run.execute(FakeConnection(), steps)
        self.assertEqual(written["entity_match"], len(self.result["matches"]))
        self.assertEqual(written["match_run"], 1)


class TestSummary(unittest.TestCase):
    def test_summary_counts_agree_with_the_run(self):
        result = sample_result()
        summary = load_run.summarize(result)
        self.assertEqual(
            summary["confirmed"] + summary["pending"] + summary["rejected"], len(result["matches"])
        )
        self.assertEqual(summary["estates"], len(result["estates"]))


class TestCommandLine(unittest.TestCase):
    def test_dry_run_writes_nothing_and_names_no_names(self):
        result = sample_result()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "run.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f)

            import io
            from contextlib import redirect_stdout

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = load_run.main(["--run", path, "--dry-run"])
        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        self.assertIn("would write", output)
        # a dry run ends up in a terminal log; it must not print a decedent
        for match in result["matches"]:
            self.assertNotIn(match["decedent_name"], output)
            self.assertNotIn(match["right_id"], output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
