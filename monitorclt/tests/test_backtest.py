"""Backtest harness tests over a hand-built scenario with known answers.

The metrics have to be arithmetically right or the whole exercise misleads, so
this file checks exact counts rather than plausibility.
"""

import sys
import unittest
from datetime import date, datetime, timezone
from os.path import dirname, join

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.backtest.harness import outcomes_in_window, run_backtest  # noqa: E402
from monitorclt.backtest.reconstruct import owner_states_as_of  # noqa: E402
from monitorclt.backtest.signals import SIGNALS  # noqa: E402
from monitorclt.normalize.owner import OwnerType  # noqa: E402
from monitorclt.parcel.model import Parcel  # noqa: E402
from monitorclt.parcel.store import ParcelStore  # noqa: E402
from monitorclt.sales.model import Sale  # noqa: E402
from monitorclt.sales.store import upsert_sales  # noqa: E402

OBSERVED_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)
AS_OF = date(2022, 1, 1)
HORIZON_END = date(2023, 12, 31)


def make_sale(property_id, sale_date, grantor, grantee, validity=" ", nal=None):
    return Sale.from_arcgis(
        {
            "propertyid": property_id,
            "parcelid": "P" + str(property_id),
            "saledate": int(
                datetime(
                    sale_date.year, sale_date.month, sale_date.day, tzinfo=timezone.utc
                ).timestamp()
                * 1000
            ),
            "saleprice": 250000.0,
            "grantor": grantor,
            "grantee": grantee,
            "salesvalidity": validity,
            "naldesc": nal,
            "soldasvacantflag": "No",
        },
        observed_at=OBSERVED_AT,
    )


def build_store():
    """Five properties with known histories.

    | id | history                                      | at 2022-01-01      | 2022-23 outcome |
    |----|----------------------------------------------|--------------------|-----------------|
    | 1  | 2000 -> SMITH JOHN                           | person, 22y        | none            |
    | 2  | 1990 -> DOE JANE; 2023 sold by ESTATE OF ... | person, 32y        | estate sale     |
    | 3  | 2021 -> ACME LLC                             | company, 0.7y      | none            |
    | 4  | 1985 -> BROWN BOB; 2023 arms-length sale     | person, 36.9y      | arms-length     |
    | 5  | first sale 2024 only                         | (unreconstructable)| outside window  |
    """
    store = ParcelStore(":memory:")
    sales = [
        make_sale(1, date(2000, 5, 1), "PRIOR OWNER", "SMITH JOHN"),
        make_sale(2, date(1990, 3, 15), "PRIOR OWNER", "DOE JANE"),
        make_sale(2, date(2023, 6, 10), "ESTATE OF JANE DOE", "BUYER ONE LLC"),
        make_sale(3, date(2021, 4, 20), "PRIOR OWNER", "ACME HOLDINGS LLC"),
        make_sale(4, date(1985, 2, 2), "PRIOR OWNER", "BROWN BOB"),
        make_sale(4, date(2023, 1, 30), "BROWN BOB", "BUYER TWO"),
        make_sale(5, date(2024, 7, 1), "PRIOR OWNER", "LATE ARRIVAL"),
    ]
    upsert_sales(store, [s for s in sales if s is not None])
    # Parcels are only needed for the "uncovered" denominator.
    for property_id in range(1, 6):
        store.upsert(
            [
                Parcel.from_arcgis(
                    {
                        "camapid": "C" + str(property_id),
                        "propertyid": property_id,
                        "full_owner_name": "PLACEHOLDER OWNER",
                        "situsaddress1": str(property_id) + " TEST ST CHARLOTTE NC",
                    },
                    observed_at=OBSERVED_AT,
                    source="synthetic",
                )
            ]
        )
    return store


class TestReconstruction(unittest.TestCase):
    def setUp(self):
        self.store = build_store()

    def tearDown(self):
        self.store.close()

    def test_owner_is_grantee_of_last_sale_before_as_of(self):
        states = owner_states_as_of(self.store, AS_OF)
        self.assertEqual(states[1].owner, "SMITH JOHN")
        self.assertEqual(states[3].owner, "ACME HOLDINGS LLC")

    def test_future_sale_does_not_leak_into_reconstructed_state(self):
        # Property 2 sells in 2023. As of 2022 the owner must still be DOE JANE,
        # not the 2023 buyer -- this is the leak the whole module exists to avoid.
        states = owner_states_as_of(self.store, AS_OF)
        self.assertEqual(states[2].owner, "DOE JANE")
        self.assertEqual(states[2].owned_since, date(1990, 3, 15))
        self.assertNotEqual(states[2].owner, "BUYER ONE LLC")

    def test_property_without_a_prior_sale_is_absent_not_guessed(self):
        self.assertNotIn(5, owner_states_as_of(self.store, AS_OF))

    def test_tenure_is_measured_from_the_as_of_date(self):
        states = owner_states_as_of(self.store, AS_OF)
        self.assertAlmostEqual(states[1].tenure_years, 21.7, places=1)
        self.assertAlmostEqual(states[4].tenure_years, 36.9, places=1)

    def test_owner_type_is_classified_from_the_grantee(self):
        states = owner_states_as_of(self.store, AS_OF)
        self.assertTrue(states[1].is_individual)
        self.assertEqual(states[3].owner_type, OwnerType.COMPANY)
        self.assertFalse(states[3].is_individual)

    def test_reconstruction_is_deterministic_across_runs(self):
        first = owner_states_as_of(self.store, AS_OF)
        second = owner_states_as_of(self.store, AS_OF)
        self.assertEqual(
            {k: v.owner for k, v in first.items()}, {k: v.owner for k, v in second.items()}
        )


class TestOutcomes(unittest.TestCase):
    def setUp(self):
        self.store = build_store()

    def tearDown(self):
        self.store.close()

    def test_window_is_exclusive_of_the_as_of_date_and_inclusive_of_the_end(self):
        outcomes = outcomes_in_window(self.store, AS_OF, HORIZON_END)
        self.assertEqual(set(outcomes), {2, 4})

    def test_estate_grantor_is_detected_as_an_estate_sale(self):
        outcomes = outcomes_in_window(self.store, AS_OF, HORIZON_END)
        self.assertTrue(outcomes[2].estate_sale)
        self.assertFalse(outcomes[4].estate_sale)

    def test_sale_outside_the_window_is_excluded(self):
        self.assertNotIn(5, outcomes_in_window(self.store, AS_OF, HORIZON_END))


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.store = build_store()
        self.report = run_backtest(self.store, as_of=AS_OF, horizon_end=HORIZON_END)

    def tearDown(self):
        self.store.close()

    def result(self, signal, outcome):
        return next(
            r for r in self.report.results if r.signal == signal and r.outcome == outcome
        )

    def test_population_excludes_unreconstructable_properties(self):
        self.assertEqual(self.report.states_reconstructed, 4)
        self.assertEqual(self.report.parcels_in_index, 5)
        self.assertEqual(self.report.uncovered, 1)

    def test_base_rate_arithmetic(self):
        # Properties 2 and 4 sold; population is 4.
        result = self.result("tenure_30y", "any_sale")
        self.assertEqual(result.outcome_total, 2)
        self.assertEqual(result.population, 4)
        self.assertAlmostEqual(result.base_rate, 0.5)

    def test_precision_recall_and_lift(self):
        # tenure_30y flags properties 2 (32y) and 4 (36.9y); both sold.
        result = self.result("tenure_30y", "any_sale")
        self.assertEqual(result.signal_size, 2)
        self.assertEqual(result.true_positives, 2)
        self.assertAlmostEqual(result.precision, 1.0)
        self.assertAlmostEqual(result.recall, 1.0)
        self.assertAlmostEqual(result.lift, 2.0)
        self.assertAlmostEqual(result.coverage, 0.5)

    def test_control_signal_has_no_predictive_value_here(self):
        # Property 3 bought in 2021 is the only recent buyer, and it did not sell.
        result = self.result("recently_bought_under_3y", "any_sale")
        self.assertEqual(result.signal_size, 1)
        self.assertEqual(result.true_positives, 0)
        self.assertAlmostEqual(result.lift, 0.0)

    def test_estate_outcome_is_attributed_to_the_right_signal(self):
        result = self.result("individual_tenure_30y", "estate_sale")
        self.assertEqual(result.outcome_total, 1)
        self.assertEqual(result.true_positives, 1)

    def test_lift_of_one_means_no_signal(self):
        # individual_owner flags 1, 2 and 4; two of three sold, base rate 50%.
        result = self.result("individual_owner", "any_sale")
        self.assertEqual(result.signal_size, 3)
        self.assertEqual(result.true_positives, 2)
        self.assertAlmostEqual(result.precision, 2.0 / 3.0)
        self.assertAlmostEqual(result.lift, (2.0 / 3.0) / 0.5)

    def test_every_signal_is_reported_for_every_outcome(self):
        self.assertEqual(len(self.report.results), len(SIGNALS) * 4)

    def test_zero_division_is_safe_for_an_empty_signal(self):
        result = self.result("decedent_marked_owner", "any_sale")
        self.assertEqual(result.signal_size, 0)
        self.assertEqual(result.precision, 0.0)
        self.assertEqual(result.lift, 0.0)

    def test_lead_time_is_measured_from_the_as_of_date(self):
        # individual_tenure_30y flags properties 2 and 4; both had arms-length
        # sales in the window (2023-06-10 and 2023-01-30), so the median is of
        # both lead times, not just the earlier one.
        median = self.report.lead_time_days["individual_tenure_30y"]
        self.assertIsNotNone(median)
        expected = [
            (date(2023, 1, 30) - AS_OF).days,
            (date(2023, 6, 10) - AS_OF).days,
        ]
        self.assertAlmostEqual(median, sum(expected) / 2.0)

    def test_lead_time_is_none_when_a_signal_catches_nothing(self):
        # Nobody in the scenario has held for 40 years.
        self.assertIsNone(self.report.lead_time_days["tenure_40y"])


if __name__ == "__main__":
    unittest.main()
