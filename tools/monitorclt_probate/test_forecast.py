#!/usr/bin/env python3
"""Tests for the pipeline forecast.

Run directly (`python3 test_forecast.py`) or under pytest from the repo root.
"""

import csv
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import forecast  # noqa: E402
import store  # noqa: E402

SAMPLE = os.path.join(HERE, "sample")
PULL_2 = os.path.join(SAMPLE, "pull_2026-09")


def load(conn, directory, as_of):
    estates = crossref.load_records(os.path.join(directory, "estate_cases.jsonl"))
    parcels = crossref.load_records(os.path.join(directory, "parcels.jsonl"))
    deeds = crossref.load_records(os.path.join(directory, "deeds.jsonl"))
    rules = crossref.load_rules()
    return store.load_run(conn, crossref.crossref(estates, parcels, deeds, rules), estates, parcels, deeds, rules, as_of=as_of)


def two_pull_store():
    conn = store.connect(":memory:")
    load(conn, SAMPLE, "2026-04-15")
    load(conn, PULL_2, "2026-09-01")
    return conn


class Periods(unittest.TestCase):
    def test_month_and_week_labels(self):
        self.assertEqual(forecast.period_of("2026-03-14", "month"), "2026-03")
        self.assertEqual(forecast.period_of("2026-03-14", "week"), "2026-W11")
        self.assertIsNone(forecast.period_of(None, "month"))

    def test_next_period_rolls_over(self):
        self.assertEqual(forecast.next_period("2026-12", "month"), "2027-01")
        self.assertEqual(forecast.next_period("2026-W52", "week"), "2026-W53")
        self.assertEqual(forecast.next_period("2026-W53", "week"), "2027-W01")

    def test_periods_between_is_inclusive_and_gap_free(self):
        self.assertEqual(forecast.periods_between("2026-11", "2027-02", "month"), ["2026-11", "2026-12", "2027-01", "2027-02"])


class HistoryFromTheStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = two_pull_store()
        cls.fc = forecast.forecast(cls.conn, periods=3)

    def test_every_month_from_first_filing_to_last_pull_is_present(self):
        self.assertEqual([r["period"] for r in self.fc["history"]], ["2026-0{0}".format(m) for m in range(1, 10)])
        self.assertFalse(self.fc["history"][-1]["complete"])
        self.assertTrue(all(r["complete"] for r in self.fc["history"][:-1]))

    def test_filings_bucket_by_filing_date_and_split_by_outcome(self):
        by = {r["period"]: r for r in self.fc["history"]}
        self.assertEqual((by["2026-01"]["filings"], by["2026-01"]["lead_estates"], by["2026-01"]["review_only_estates"]), (3, 2, 1))
        # March: David Smith (review only), Alice, George, "Johnson" -- no lead among them.
        self.assertEqual((by["2026-03"]["filings"], by["2026-03"]["lead_estates"], by["2026-03"]["no_candidate_estates"]), (4, 0, 3))
        self.assertEqual(by["2026-08"]["lead_estates"], 1)  # Helen

    def test_retirements_land_on_the_deed_date_not_the_pull_date(self):
        by = {r["period"]: r for r in self.fc["history"]}
        self.assertEqual(by["2026-04"]["retired"], 1)  # Robert: executor deed 2026-04-02
        self.assertEqual(by["2026-07"]["retired"], 1)  # Mary: administrator deed 2026-07-10
        self.assertEqual(by["2026-09"]["retired"], 0)

    def test_active_leads_is_arrivals_minus_retirements(self):
        self.assertEqual([r["active_lead_estates"] for r in self.fc["history"]], [2, 3, 3, 2, 2, 2, 1, 2, 2])

    def test_new_parcels_count_only_parcels_that_were_confirmed(self):
        by = {r["period"]: r for r in self.fc["history"]}
        self.assertEqual((by["2026-04"]["new_confirmed_parcels"], by["2026-09"]["new_confirmed_parcels"]), (3, 1))


class Rates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rates = forecast.forecast(two_pull_store(), periods=1)["rates"]

    def test_rates_carry_their_denominators(self):
        self.assertEqual(self.rates["lead_yield"], {"rate": round(4 / 9, 4), "numerator": 4, "denominator": 9})
        self.assertEqual(self.rates["review_yield"]["numerator"], 2)
        self.assertEqual(self.rates["parcels_per_lead_estate"], 1.0)

    def test_filings_band_uses_complete_periods_only(self):
        fp = self.rates["filings_per_period"]
        self.assertEqual((fp["complete_periods"], fp["min"], fp["max"]), (8, 0, 4))
        self.assertAlmostEqual(fp["mean"], 9 / 8, places=2)

    def test_retirement_hazard_is_per_lead_period_of_exposure(self):
        rp = self.rates["retirement_per_period"]
        self.assertEqual(rp["retired"], 2)
        self.assertGreater(rp["lead_periods_observed"], 0)
        self.assertLess(rp["rate"], 1.0)

    def test_lags_are_medians_in_days(self):
        self.assertIsInstance(self.rates["median_days_filing_to_lead"], (int, float))
        self.assertIsInstance(self.rates["median_days_filing_to_retired"], (int, float))


class Projection(unittest.TestCase):
    def setUp(self):
        self.conn = two_pull_store()

    def test_projection_continues_from_the_last_period(self):
        fc = forecast.forecast(self.conn, periods=3)
        self.assertEqual([r["period"] for r in fc["projection"]], ["2026-10", "2026-11", "2026-12"])

    def test_low_base_high_follow_the_filings_band(self):
        row = forecast.forecast(self.conn, periods=1)["projection"][0]
        self.assertEqual((row["low"]["filings"], row["high"]["filings"]), (0, 4))
        self.assertLessEqual(row["low"]["new_leads"], row["base"]["new_leads"])
        self.assertLessEqual(row["base"]["new_leads"], row["high"]["new_leads"])

    def test_given_filings_collapse_the_band(self):
        fc = forecast.forecast(self.conn, periods=2, filings=400)
        row = fc["projection"][0]
        self.assertEqual((row["low"]["filings"], row["base"]["filings"], row["high"]["filings"]), (400, 400, 400))
        self.assertAlmostEqual(row["base"]["new_leads"], 400 * 4 / 9, places=0)
        self.assertIn("given", fc["filings_assumption"])

    def test_inventory_decays_and_refills(self):
        rows = forecast.forecast(self.conn, periods=12, filings=0)["projection"]
        active = [r["base"]["active_leads"] for r in rows]
        self.assertEqual(active, sorted(active, reverse=True))
        self.assertLess(active[-1], active[0])

    def test_spend_is_leads_times_cost(self):
        row = forecast.forecast(self.conn, periods=1, filings=100, cost_per_lead=10, cost_per_review=1)["projection"][0]["base"]
        self.assertAlmostEqual(row["spend"], row["new_leads"] * 10 + row["reviews"] * 1, delta=1.0)  # per-field rounding
        self.assertEqual(forecast.forecast(self.conn, periods=1)["projection"][0]["base"]["spend"], 0)

    def test_empty_store_has_no_history_and_no_projection(self):
        fc = forecast.forecast(store.connect(":memory:"))
        self.assertEqual((fc["history"], fc["rates"], fc["projection"]), ([], None, []))


class Outputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fc = forecast.forecast(two_pull_store(), periods=2, cost_per_lead=45)

    def test_text_report(self):
        text = forecast.format_report(self.fc)
        for needle in ("HISTORY", "RATES", "PROJECTION", "total spend", "(partial)"):
            self.assertIn(needle, text)

    def test_csv_has_actuals_then_three_scenarios_per_forecast_period(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        handle.close()
        try:
            forecast.write_csv(handle.name, self.fc)
            with open(handle.name, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        finally:
            os.unlink(handle.name)
        self.assertEqual(sum(1 for r in rows if r["kind"] == "actual"), 9)
        self.assertEqual(sorted(r["scenario"] for r in rows if r["period"] == "2026-10"), ["base", "high", "low"])

    def test_html_is_self_contained_and_escaped(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
        handle.close()
        try:
            forecast.write_html(handle.name, self.fc)
            with open(handle.name, encoding="utf-8") as f:
                page = f.read()
        finally:
            os.unlink(handle.name)
        self.assertIn("<!doctype html>", page)
        self.assertNotIn("<script", page)
        self.assertIn("2026-10", page)
        self.assertIn("Projected spend", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
