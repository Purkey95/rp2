import os
import unittest

from mclt_helpers import FIXTURES, resolved

from monitorclt import evaluate, review
from monitorclt.resolve import resolve, resolver
from monitorclt.resolve.model import load_rules


class ReviewLoopTests(unittest.TestCase):
    def test_decision_writes_status_label_audit_event(self):
        s = resolved()
        q = review.queue(s)
        self.assertTrue(all(m["status"] == "pending" for m in q))
        self.assertTrue(q[0]["probability"] >= q[-1]["probability"])
        m = review.decide(s, q[0]["id"], "confirm", "alice", "looks right", 8.5)
        self.assertEqual((m["status"], m["decided_by"], m["reviewer"]), ("confirmed", "reviewer", "alice"))
        self.assertIsNotNone(m["person_id"])
        lab = s.one("SELECT * FROM label WHERE left_id = ? AND right_id = ?", (m["left_id"], m["right_id"]))
        self.assertEqual((lab["is_match"], lab["origin"], lab["labeled_by"]), (1, "review", "alice"))
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM review_decision WHERE match_id = ?", (m["id"],)), 1)
        self.assertEqual(s.scalar("SELECT action FROM access_log ORDER BY id DESC LIMIT 1"), "review.confirm")
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'match_confirmed' AND source = 'review'"), 1)

    def test_skip_changes_nothing_but_is_logged(self):
        s = resolved()
        q = review.queue(s)
        m = review.decide(s, q[0]["id"], "skip", "alice")
        self.assertEqual(m["status"], "pending")
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM label"), 0)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM review_decision"), 1)

    def test_reject_then_reconfirm_updates_label(self):
        s = resolved()
        mid = review.queue(s)[0]["id"]
        review.decide(s, mid, "reject", "alice")
        review.decide(s, mid, "confirm", "bob")
        lab = s.one("SELECT is_match, labeled_by FROM label")
        self.assertEqual((lab["is_match"], lab["labeled_by"]), (1, "bob"))

    def test_detail_has_everything_on_one_screen(self):
        s = resolved()
        d = review.detail(s, review.queue(s)[0]["id"])
        self.assertEqual(set(d), {"match", "left", "right", "deeds", "parcel_events", "contributions", "decisions"})
        self.assertIn("decedent_name", d["left"]["record"])
        self.assertIn("owner_name", d["right"]["record"])
        self.assertTrue(d["contributions"])
        self.assertIsNotNone(d["right"]["parcel"])

    def test_bad_decision(self):
        s = resolved()
        with self.assertRaises(ValueError):
            review.decide(s, review.queue(s)[0]["id"], "maybe", "alice")
        with self.assertRaises(KeyError):
            review.decide(s, 999, "confirm", "alice")


class LabelsTrainEvaluateTests(unittest.TestCase):
    def test_import_labels_resolves_ids(self):
        s = resolved()
        r = review.import_labels(s, os.path.join(FIXTURES, "labels.csv"))
        self.assertEqual((r["added"], r["problems"]), (14, []))
        rows, unscored = review.training_rows(s)
        self.assertEqual((len(rows), len(unscored)), (14, 0))
        self.assertEqual(s.scalar("SELECT right_id FROM label WHERE left_id LIKE '%001239'"), "UNION/07190225")

    def test_train_improves_calibration_and_is_active(self):
        s = resolved()
        review.import_labels(s, os.path.join(FIXTURES, "labels.csv"))
        before = evaluate.evaluate(s)["reliability"]["brier"]
        r = review.train(s)
        self.assertIsNotNone(r["version_id"])
        self.assertLess(r["metrics"]["reliability"]["brier"], before)
        self.assertEqual(s.scalar("SELECT active FROM model_version WHERE id = ?", (r["version_id"],)), 1)
        run = resolve(s)
        self.assertEqual(run["model_version_id"], r["version_id"])

    def test_train_without_labels(self):
        s = resolved()
        self.assertIsNone(review.train(s)["version_id"])

    def test_evaluate_two_operating_points_and_miss_reasons(self):
        s = resolved()
        review.import_labels(s, os.path.join(FIXTURES, "labels.csv"))
        ev = evaluate.evaluate(s, target_precision=0.95)
        self.assertEqual(ev["current"]["auto_confirm"]["fp"], 0)
        self.assertEqual(ev["current"]["auto_confirm"]["precision"], 1.0)
        self.assertGreater(ev["current"]["through_review"]["recall"], ev["current"]["auto_confirm"]["recall"])
        reasons = {m["reason"] for m in ev["current"]["misses"]}
        self.assertEqual(reasons, {"scored_low"})
        self.assertIsNotNone(ev["recommendation"]["lowest_threshold_clearing_target"])
        text = evaluate.format_report(ev)
        for needle in ("auto-confirm", "through review", "threshold sweep", "calibration", "scored_low"):
            self.assertIn(needle, text)

    def test_evaluate_reports_blocked_out(self):
        s = resolved()
        s.execute(
            "INSERT INTO label (kind, left_id, right_id, is_match, origin, labeled_at) VALUES ('estate_case->parcel', 'MECKLENBURG/26 E 001240', 'MECKLENBURG/13307013', 1, 'seed', 'x')"
        )
        ev = evaluate.evaluate(s)
        self.assertEqual([m["reason"] for m in ev["current"]["misses"]], ["blocked_out"])

    def test_status_at_sweep_is_monotone(self):
        s = resolved()
        rules = load_rules()
        m = resolver.matches(s, status="confirmed")[0]
        self.assertEqual(evaluate.status_at(m, 0.5, rules), "confirmed")
        self.assertEqual(evaluate.status_at(m, 0.999, rules), "pending")


if __name__ == "__main__":
    unittest.main()
