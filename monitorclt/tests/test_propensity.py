"""Propensity model and validation tests.

The label-leak test is the important one here: an early run of this analysis
sorted rows as ``(score, outcome)``, which orders positives first inside every
tied block and manufactured a 2.56x top decile with impossible 0.00% deciles
in the middle. Cell models produce few distinct scores, so most of the
population sits in ties and the leak is easy to reintroduce.
"""

import sys
import unittest
from os.path import dirname, join

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.propensity.model import (  # noqa: E402
    PropensityModel,
    tenure_bucket,
)
from monitorclt.propensity.validate import validate  # noqa: E402


class TestTenureBuckets(unittest.TestCase):
    def test_bucket_edges(self):
        self.assertEqual(tenure_bucket(0.0), "0-3")
        self.assertEqual(tenure_bucket(2.99), "0-3")
        self.assertEqual(tenure_bucket(3.0), "3-7")
        self.assertEqual(tenure_bucket(19.9), "12-20")
        self.assertEqual(tenure_bucket(20.0), "20-30")
        self.assertEqual(tenure_bucket(30.0), "30+")
        self.assertEqual(tenure_bucket(120.0), "30+")


class TestModelFit(unittest.TestCase):
    def test_rate_reflects_training_outcomes(self):
        # Two cells, so the prior (0.45) differs from either cell rate and
        # smoothing has something to pull toward.
        examples = [(("PERSON", "0-3"), i < 800) for i in range(1000)]
        examples += [(("PERSON", "30+"), i < 100) for i in range(1000)]
        model = PropensityModel.fit(examples)
        high = model.predict(("PERSON", "0-3"))
        low = model.predict(("PERSON", "30+"))
        self.assertAlmostEqual(model.prior, 0.45)
        self.assertGreater(high, low)
        # Each cell sits between its raw rate and the prior.
        self.assertLess(high, 0.80)
        self.assertGreater(high, 0.75)
        self.assertGreater(low, 0.10)
        self.assertLess(low, 0.15)

    def test_thin_cells_are_pulled_toward_the_prior(self):
        # One cell has 2 rows both positive; another has 2,000 rows at 10%.
        examples = [(("COMPANY", "0-3"), True)] * 2
        examples += [(("PERSON", "30+"), i < 200) for i in range(2000)]
        model = PropensityModel.fit(examples)
        thin = model.predict(("COMPANY", "0-3"))
        self.assertLess(thin, 0.5, "a 2-row cell must not score like a certainty")
        self.assertGreater(thin, model.prior)

    def test_unseen_cell_falls_back_to_the_prior(self):
        model = PropensityModel.fit([(("PERSON", "0-3"), True), (("PERSON", "0-3"), False)])
        self.assertAlmostEqual(model.predict(("TRUST", "30+")), model.prior)

    def test_empty_training_set_is_safe(self):
        model = PropensityModel.fit([])
        self.assertEqual(model.predict(("PERSON", "0-3")), 0.0)

    def test_support_is_retained_for_auditing(self):
        model = PropensityModel.fit([(("PERSON", "0-3"), True)] * 7)
        self.assertEqual(model.support[("PERSON", "0-3")], 7)


class TestValidationDoesNotLeakLabels(unittest.TestCase):
    def test_a_model_with_no_information_scores_flat(self):
        """The regression test for the tie-break leak.

        Every row shares one cell, so every predicted score is identical and
        the ranking carries no information. A correct implementation returns
        deciles at roughly 1.00x. The leaking version returned 2.5x+ on decile
        one and 0.00x in the middle.
        """
        examples = [(("PERSON", "0-3"), index % 4 == 0) for index in range(4000)]
        model = PropensityModel.fit(examples)
        report = validate(model, examples)
        self.assertEqual(report.distinct_scores, 1)
        for row in report.deciles:
            self.assertLess(
                abs(row.lift - 1.0), 0.35, msg="decile " + str(row.decile) + " leaked"
            )

    def test_no_decile_is_impossibly_empty_under_ties(self):
        examples = [(("PERSON", "0-3"), index % 3 == 0) for index in range(3000)]
        model = PropensityModel.fit(examples)
        for row in validate(model, examples).deciles:
            self.assertGreater(row.actual, 0.0)

    def test_validation_is_deterministic_for_a_given_seed(self):
        examples = [(("PERSON", "0-3"), index % 5 == 0) for index in range(2000)]
        model = PropensityModel.fit(examples)
        first = validate(model, examples, seed=7)
        second = validate(model, examples, seed=7)
        self.assertEqual(
            [r.actual for r in first.deciles], [r.actual for r in second.deciles]
        )


class TestValidationMetrics(unittest.TestCase):
    def setUp(self):
        # A genuinely separable problem: one cell always sells, one never does.
        self.examples = (
            [(("COMPANY", "0-3"), True)] * 1000 + [(("PERSON", "30+"), False)] * 9000
        )
        self.model = PropensityModel.fit(self.examples)
        self.report = validate(self.model, self.examples)

    def test_a_real_signal_concentrates_in_the_top_decile(self):
        self.assertGreater(self.report.top_decile_lift, 9.0)

    def test_top_decile_recall_is_complete_when_the_signal_is_perfect(self):
        self.assertAlmostEqual(self.report.top_decile_recall, 1.0, places=2)

    def test_base_rate_and_population(self):
        self.assertAlmostEqual(self.report.base_rate, 0.1)
        self.assertEqual(self.report.population, 10000)

    def test_cumulative_recall_reaches_one(self):
        self.assertAlmostEqual(self.report.deciles[-1].cumulative_recall, 1.0, places=6)

    def test_empty_examples_are_safe(self):
        self.assertEqual(validate(self.model, []).deciles, ())


if __name__ == "__main__":
    unittest.main()
