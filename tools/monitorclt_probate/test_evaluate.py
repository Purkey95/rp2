#!/usr/bin/env python3
"""Tests for the calibration harness.

Run directly (`python3 test_evaluate.py`) or under pytest from the repo root.
"""

import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)  # pylint: disable=wrong-import-position
import evaluate  # noqa: E402  # pylint: disable=wrong-import-position

SAMPLE = os.path.join(HERE, "sample")


def rules():
    return crossref.load_rules()


def load_sample():
    return (
        crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
    )


def write_labels(rows):
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="") as handle:
        handle.write("file_number,pin,is_match\n")
        for row in rows:
            handle.write(",".join(row) + "\n")
    return handle.name


class TestLabelLoading(unittest.TestCase):
    def setUp(self):
        self.estates, self.parcels, _ = load_sample()

    def test_bare_ids_resolve_to_county_qualified_ids(self):
        path = write_labels([("26 E 001234", "045-121-08", "1")])
        try:
            labels, unresolved = evaluate.load_labels(path, self.estates, self.parcels)
        finally:
            os.unlink(path)
        self.assertEqual(unresolved, [])
        self.assertEqual(labels, {("MECKLENBURG/26 E 001234", "MECKLENBURG/045-121-08"): True})

    def test_pins_differing_only_in_punctuation_still_resolve(self):
        path = write_labels([("26E001234", "04512108", "1")])
        try:
            labels, _ = evaluate.load_labels(path, self.estates, self.parcels)
        finally:
            os.unlink(path)
        self.assertIn(("MECKLENBURG/26 E 001234", "MECKLENBURG/045-121-08"), labels)

    def test_a_pin_in_two_counties_is_an_error_not_a_guess(self):
        parcels = self.parcels + [dict(self.parcels[0], county="UNION")]
        path = write_labels([("26 E 001234", "045-121-08", "1")])
        try:
            with self.assertRaises(ValueError):
                evaluate.load_labels(path, self.estates, parcels)
        finally:
            os.unlink(path)

    def test_rows_naming_unknown_records_are_reported_not_dropped(self):
        path = write_labels([("26 E 999999", "045-121-08", "1")])
        try:
            labels, unresolved = evaluate.load_labels(path, self.estates, self.parcels)
        finally:
            os.unlink(path)
        self.assertEqual(labels, {})
        self.assertEqual(len(unresolved), 1)
        self.assertIn("estate case", unresolved[0]["reason"])

    def test_unparseable_is_match_is_reported_not_guessed(self):
        path = write_labels([("26 E 001234", "045-121-08", "maybe")])
        try:
            labels, unresolved = evaluate.load_labels(path, self.estates, self.parcels)
        finally:
            os.unlink(path)
        self.assertEqual(labels, {})
        self.assertEqual(unresolved[0]["reason"], "is_match not 1/0")


class TestScoring(unittest.TestCase):
    # Declared for the type checker: setUpClass assigns these on the class.
    estates: List[Any]
    parcels: List[Any]
    deeds: List[Any]
    rules: Dict[str, Any]
    result: Dict[str, Any]
    labels: Dict[Any, Any]
    unresolved: List[Any]

    @classmethod
    def setUpClass(cls):
        cls.estates, cls.parcels, cls.deeds = load_sample()
        cls.rules = rules()
        cls.result = crossref.crossref(cls.estates, cls.parcels, cls.deeds, cls.rules)
        cls.labels, cls.unresolved = evaluate.load_labels(os.path.join(SAMPLE, "labels.csv"), cls.estates, cls.parcels)

    def test_status_at_the_shipped_threshold_matches_what_crossref_decided(self):
        threshold = self.rules["thresholds"]["auto_confirm"]
        for link in self.result["matches"]:
            self.assertEqual(
                evaluate.status_at(link, threshold, self.rules),
                link["status"],
                msg=link["right_id"],
            )

    def test_sweep_agrees_with_the_shipped_threshold_row(self):
        # Regression: accumulating floats made 0.85 arrive as 0.8500000000000001,
        # silently dropping every link scoring exactly 0.850.
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        shipped = self.rules["thresholds"]["auto_confirm"]
        row = [p for p in ev["sweep"] if abs(p["threshold"] - shipped) < 1e-9]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["counts"], ev["at_current_threshold"]["counts"])

    def test_sweep_starts_at_the_review_floor(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        self.assertEqual(ev["sweep"][0]["threshold"], self.rules["thresholds"]["review_floor"])

    def test_sample_labels_give_perfect_precision_and_half_recall(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        point = ev["at_current_threshold"]
        self.assertEqual(point["counts"], {"tp": 3, "fp": 0, "fn": 3, "tn": 8})
        self.assertEqual(point["precision"], 1.0)
        self.assertEqual(point["recall"], 0.5)
        self.assertEqual(point["review_recall"], 1.0)

    def test_missed_matches_are_split_by_cause(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        point = ev["at_current_threshold"]
        self.assertEqual(point["blocked_out"], [])  # every true match was a candidate
        self.assertEqual(len(point["scored_low"]), 3)

    def test_a_true_match_that_never_blocked_in_counts_as_blocked_out(self):
        labels = dict(self.labels)
        labels[("MECKLENBURG/26 E 001240", "MECKLENBURG/133-070-13")] = True
        point = evaluate.confusion(self.result["matches"], labels, self.rules["thresholds"]["auto_confirm"], self.rules)
        self.assertEqual(
            point["blocked_out"],
            [{"left_id": "MECKLENBURG/26 E 001240", "right_id": "MECKLENBURG/133-070-13"}],
        )
        self.assertEqual(point["counts"]["fn"], 4)

    def test_evidence_breakdown_credits_every_label_a_link_carries(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        by_name = {row["name"]: row for row in ev["by_evidence"]}
        self.assertEqual(by_name["name_full_exact"]["tp"], 3)
        self.assertEqual(by_name["deed_grantor_link"]["precision"], 1.0)

    def test_an_unreachable_precision_target_yields_no_recommendation(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 1.01)
        self.assertIsNone(ev["recommendation"]["lowest_threshold_meeting_target"])

    def test_report_renders(self):
        ev = evaluate.evaluate(self.result, self.labels, self.unresolved, self.rules, 0.95)
        text = evaluate.format_report(ev)
        self.assertIn("AT THE SHIPPED THRESHOLD", text)
        self.assertIn("THRESHOLD SWEEP", text)
        self.assertIn("RECOMMENDATION", text)


class TestLabelRegressions(unittest.TestCase):
    """Label-file defects that corrupted the calibration numbers themselves."""

    estates: List[Any]
    parcels: List[Any]
    deeds: List[Any]

    @classmethod
    def setUpClass(cls):
        cls.estates, cls.parcels, cls.deeds = load_sample()

    def _load(self, body):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
        with handle:
            handle.write(body)
        return evaluate.load_labels(handle.name, self.estates, self.parcels)

    def test_a_qualified_id_naming_no_record_is_reported_not_believed(self):
        # A county-qualified id used to pass through unchecked, so a typo became
        # a phantom pair: counted as a false negative and blamed on blocking.
        labels, unresolved = self._load("file_number,pin,is_match\nNOSUCHCOUNTY/26 E 001234,MECKLENBURG/045-121-08,1\n")
        self.assertEqual(labels, {})
        self.assertEqual(len(unresolved), 1)
        self.assertIn("estate case", unresolved[0]["reason"])

    def test_a_qualified_id_resolves_regardless_of_case_or_punctuation(self):
        labels, unresolved = self._load("file_number,pin,is_match\nmecklenburg/26e001234,mecklenburg/04512108,1\n")
        self.assertEqual(unresolved, [])
        self.assertEqual(list(labels), [("MECKLENBURG/26 E 001234", "MECKLENBURG/045-121-08")])

    def test_a_row_with_more_fields_than_the_header_is_reported_not_a_crash(self):
        # An unquoted comma in a trailing note column parks the overflow under
        # DictReader's None restkey, which used to raise AttributeError.
        labels, unresolved = self._load("file_number,pin,is_match\n26 E 001234,045-121-08,1,note, with a comma\n")
        self.assertEqual(labels, {})
        self.assertEqual(len(unresolved), 1)
        self.assertIn("more fields than the header", unresolved[0]["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
