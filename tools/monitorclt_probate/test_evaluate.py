#!/usr/bin/env python3
"""Tests for the calibration harness.

Run directly (`python3 test_evaluate.py`) or under pytest from the repo root.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import evaluate  # noqa: E402

SAMPLE = os.path.join(HERE, "sample")


def rules():
    return crossref.load_rules()


def load_sample():
    return (
        crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
        crossref.load_records(os.path.join(SAMPLE, "business_entities.jsonl")),
    )


def write_labels(rows):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    )
    handle.write("file_number,pin,is_match\n")
    for row in rows:
        handle.write(",".join(row) + "\n")
    handle.close()
    return handle.name


class TestLabelLoading(unittest.TestCase):
    def setUp(self):
        self.estates, self.parcels, _, _ = load_sample()

    def test_bare_ids_resolve_to_county_qualified_ids(self):
        path = write_labels([("26 E 001234", "045-121-08", "1")])
        try:
            labels, unresolved = evaluate.load_labels(path, self.estates, self.parcels)
        finally:
            os.unlink(path)
        self.assertEqual(unresolved, [])
        self.assertEqual(
            labels, {("MECKLENBURG/26 E 001234", "MECKLENBURG/045-121-08"): True}
        )

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
    @classmethod
    def setUpClass(cls):
        cls.estates, cls.parcels, cls.deeds, cls.entities = load_sample()
        cls.rules = rules()
        cls.result = crossref.crossref(
            cls.estates, cls.parcels, cls.deeds, cls.rules, entities=cls.entities
        )
        cls.labels, cls.unresolved = evaluate.load_labels(
            os.path.join(SAMPLE, "labels.csv"), cls.estates, cls.parcels
        )

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

    def test_sample_labels_give_perfect_precision_and_full_review_recall(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        point = ev["at_current_threshold"]
        self.assertEqual(point["counts"], {"tp": 3, "fp": 0, "fn": 5, "tn": 9})
        self.assertEqual(point["precision"], 1.0)
        self.assertEqual(point["recall"], 0.375)
        self.assertEqual(point["review_recall"], 1.0)

    def test_missed_matches_are_split_by_cause(self):
        ev = evaluate.evaluate(self.result, self.labels, [], self.rules, 0.95)
        point = ev["at_current_threshold"]
        self.assertEqual(point["blocked_out"], [])  # every true match was a candidate
        self.assertEqual(len(point["scored_low"]), 3)
        self.assertEqual(len(point["capped"]), 3)

    def test_capped_links_survive_the_sweep(self):
        for link in self.result["matches"]:
            if crossref.cap_flags(link["evidence"]):
                for threshold in (0.0, 0.45, 0.85, 1.0):
                    self.assertIn(evaluate.status_at(link, threshold, self.rules), ("pending", "rejected"))

    def test_capped_positive_is_reviewable_but_never_a_true_positive(self):
        point = evaluate.confusion(self.result["matches"], self.labels, 0.0, self.rules)
        capped_positive = [r for r in point["capped"] if r["is_match"]]
        self.assertEqual(len(capped_positive), 2)
        scored_low_ids = {(r["left_id"], r["right_id"]) for r in point["scored_low"]}
        for row in capped_positive:
            self.assertNotIn((row["left_id"], row["right_id"]), scored_low_ids)
        self.assertEqual(point["review_recall"], 1.0)

    def test_capped_negative_is_never_a_false_positive_at_any_threshold(self):
        for threshold in (0.0, 0.45, 0.65, 0.85):
            point = evaluate.confusion(self.result["matches"], self.labels, threshold, self.rules)
            self.assertEqual(point["counts"]["fp"], 0)
            self.assertFalse(
                [r for r in point["false_positives"] if r["right_id"] == "MECKLENBURG/133-070-14"]
            )

    def test_a_confirmed_capped_link_fails_loudly(self):
        link = next(l for l in self.result["matches"] if "entity_official_link" in l["evidence"])
        original = crossref.disposition
        crossref.disposition = lambda *a, **k: ("confirmed", [])
        try:
            with self.assertRaises(RuntimeError):
                evaluate.status_at(link, 0.85, self.rules)
        finally:
            crossref.disposition = original

    def test_a_true_match_that_never_blocked_in_counts_as_blocked_out(self):
        labels = dict(self.labels)
        labels[("MECKLENBURG/26 E 001240", "MECKLENBURG/133-070-13")] = True
        point = evaluate.confusion(
            self.result["matches"], labels, self.rules["thresholds"]["auto_confirm"], self.rules
        )
        self.assertEqual(
            point["blocked_out"],
            [{"left_id": "MECKLENBURG/26 E 001240", "right_id": "MECKLENBURG/133-070-13"}],
        )
        self.assertEqual(point["counts"]["fn"], 6)

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
        self.assertIn("CAPPED", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
