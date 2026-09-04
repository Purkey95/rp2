#!/usr/bin/env python3
"""Tests for the point-in-time backtest.

Run directly (`python3 test_backtest.py`) or under pytest from the repo root.
"""

import csv
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import backtest  # noqa: E402  (path must be set first)
import crossref  # noqa: E402
import evaluate  # noqa: E402

PULL_2 = os.path.join(HERE, "sample", "pull_2026-09")

MARY = ("MECKLENBURG/26 E 001235", "MECKLENBURG/213-002-44")
DAVID_HOME = ("MECKLENBURG/26 E 001237", "MECKLENBURG/099-001-01")
DAVID_NAMESAKE = ("MECKLENBURG/26 E 001237", "MECKLENBURG/099-001-02")
ALICE = ("MECKLENBURG/26 E 001238", "MECKLENBURG/555-010-77")


def rules():
    return crossref.load_rules()


def load_pull():
    return (
        crossref.load_records(os.path.join(PULL_2, "estate_cases.jsonl")),
        crossref.load_records(os.path.join(PULL_2, "parcels.jsonl")),
        crossref.load_records(os.path.join(PULL_2, "deeds.jsonl")),
    )


class SplittingTheRecord(unittest.TestCase):
    def test_deeds_split_at_the_cut_off_and_undated_ones_count_as_known(self):
        known, later, undated = backtest.split_deeds(
            [{"recorded_date": "2026-01-01"}, {"recorded_date": "2026-04-15"}, {"recorded_date": "2026-04-16"}, {"recorded_date": None}], "2026-04-15"
        )
        self.assertEqual((len(known), len(later), undated), (3, 1, 1))

    def test_estates_filtered_by_filing_window(self):
        estates = [{"filing_date": "2025-12-30"}, {"filing_date": "2026-02-01"}, {"filing_date": "2026-05-01"}, {"filing_date": None}]
        self.assertEqual(len(backtest.estates_in_window(estates, "2026-01-01", "2026-04-15")), 1)
        self.assertEqual(len(backtest.estates_in_window(estates, None, "2026-04-15")), 2)


class RollingBackOwners(unittest.TestCase):
    def setUp(self):
        self.parcel = {"county": "MECKLENBURG", "pin": "1", "owner_name": "BUYER BOB", "owner_mailing_address": "BOBS HOUSE", "last_sale_date": "2026-06-01"}
        self.later = [
            {
                "county": "MECKLENBURG",
                "parcel_pin": "1",
                "recorded_date": "2026-06-01",
                "instrument_type": "EXECUTOR DEED",
                "grantor_name": "SMITH ELLEN EXECUTRIX",
                "grantee_name": "BUYER BOB",
            }
        ]
        self.known = [
            {
                "county": "MECKLENBURG",
                "parcel_pin": "1",
                "recorded_date": "2010-01-01",
                "instrument_type": "WD",
                "grantor_name": "OLD OWNER",
                "grantee_name": "SMITH DAVID",
            }
        ]

    def test_chain_of_title_is_preferred(self):
        rolled, how = backtest.roll_back_parcels([self.parcel], self.known, self.later, rules())
        self.assertEqual(rolled[0]["owner_name"], "SMITH DAVID")
        self.assertIsNone(rolled[0]["owner_mailing_address"])
        self.assertIsNone(rolled[0]["last_sale_date"])
        self.assertEqual(how, {"chain_of_title": 1})

    def test_later_grantor_is_the_fallback_and_fiduciary_grantors_are_marked(self):
        rolled, how = backtest.roll_back_parcels([self.parcel], [], self.later, rules())
        self.assertEqual(rolled[0]["owner_name"], "SMITH ELLEN EXECUTRIX")
        self.assertEqual(how, {"later_grantor_fiduciary": 1})

    def test_parcels_without_a_later_deed_are_untouched(self):
        rolled, how = backtest.roll_back_parcels([self.parcel], self.known, [], rules())
        self.assertEqual(rolled[0], self.parcel)
        self.assertEqual(how, {})

    def test_none_mode_leaves_todays_owner_in_place(self):
        rolled, how = backtest.roll_back_parcels([self.parcel], self.known, self.later, rules(), mode="none")
        self.assertEqual(rolled[0]["owner_name"], "BUYER BOB")
        self.assertEqual(how, {})


class SampleBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.estates, cls.parcels, cls.deeds = load_pull()
        cls.bt = backtest.backtest(cls.estates, cls.parcels, cls.deeds, rules(), "2026-04-15", start="2026-01-01")

    def test_window_and_split(self):
        meta = self.bt["backtest"]
        self.assertEqual((meta["estates_in_window"], meta["estates_in_file"]), (8, 9))
        self.assertEqual((meta["deeds_known"], meta["deeds_later"]), (4, 4))

    def test_labels_come_from_later_conveyances_only(self):
        labels = {(row["left_id"], row["right_id"]): row["is_match"] for row in self.bt["labels"]}
        self.assertEqual(labels, {MARY: True, DAVID_HOME: True, ALICE: True, DAVID_NAMESAKE: False})

    def test_label_provenance_names_the_instrument(self):
        by_pair = {(row["left_id"], row["right_id"]): row for row in self.bt["labels"]}
        self.assertEqual(by_pair[DAVID_NAMESAKE]["basis"], "post_death_deed_in_bare_decedent_name")
        self.assertEqual(by_pair[MARY]["grantee_relation"], "organization")
        self.assertTrue(all(row["parcel_in_index"] for row in self.bt["labels"]))

    def test_owner_reconstruction_is_reported(self):
        self.assertEqual(self.bt["backtest"]["parcels_reconstructed"], {"chain_of_title": 2, "later_grantor": 1, "later_grantor_fiduciary": 1})

    def test_the_matcher_is_scored_against_what_it_could_not_have_seen(self):
        point = self.bt["evaluation"]["at_current_threshold"]
        # Mary's HEIRS retitle had not happened at the cut-off (chain says
        # "SAMPLE MARY A"), so she is pending, not confirmed: through-review
        # recall is what a reviewer could have reached.
        self.assertEqual(point["counts"], {"tp": 0, "fp": 0, "fn": 3, "tn": 1})
        self.assertAlmostEqual(point["review_recall"], 2 / 3, places=3)
        self.assertEqual([b["right_id"] for b in point["blocked_out"]], [ALICE[1]])

    def test_initials_only_owner_is_a_genuine_blocking_miss(self):
        # 555-010-77 rolls back to its 2018 grantee "EXAMPLE A B": the estate
        # deed proves Alice held it and the matcher could never have found it.
        self.assertEqual(self.bt["backtest"]["blocked_out_unrecoverable"], 0)

    def test_without_rollback_todays_owners_hide_every_positive(self):
        bt = backtest.backtest(self.estates, self.parcels, self.deeds, rules(), "2026-04-15", rollback="none")
        point = bt["evaluation"]["at_current_threshold"]
        self.assertEqual(len(point["blocked_out"]), 3)
        self.assertEqual(point["review_recall"], 0.0)

    def test_a_cut_off_with_no_later_conveyances_scores_nothing(self):
        bt = backtest.backtest(self.estates, self.parcels, self.deeds, rules(), "2026-09-01")
        self.assertIsNone(bt["evaluation"])
        self.assertIn("nothing to score yet", backtest.format_report(bt))

    def test_report_and_summary_render(self):
        text = backtest.format_report(self.bt)
        self.assertIn("backtest as of 2026-04-15", text)
        self.assertIn("BACKTEST CAVEATS", text)
        self.assertIn("BACKTEST SUMMARY", backtest.format_summary([self.bt]))

    def test_written_labels_load_back_into_evaluate(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        handle.close()
        try:
            backtest.write_labels(handle.name, [self.bt])
            with open(handle.name, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 4)
            labels, unresolved = evaluate.load_labels(handle.name, self.estates, self.parcels)
            self.assertEqual(len(labels), 4)
            self.assertEqual(unresolved, [])
        finally:
            os.unlink(handle.name)


class BacktestOverTime(unittest.TestCase):
    def test_earlier_cut_offs_see_fewer_estates_and_fewer_answers(self):
        estates, parcels, deeds = load_pull()
        runs = [backtest.backtest(estates, parcels, deeds, rules(), as_of, start="2026-01-01") for as_of in ("2026-01-31", "2026-04-15")]
        self.assertEqual([bt["backtest"]["estates_in_window"] for bt in runs], [3, 8])
        self.assertEqual([bt["backtest"]["labels_positive"] for bt in runs], [1, 3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
