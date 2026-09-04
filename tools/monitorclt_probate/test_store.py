#!/usr/bin/env python3
"""Tests for the run store, the loader delta, transfer detection and the reports.

Run directly (`python3 test_store.py`) or under pytest from the repo root.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import report  # noqa: E402
import store  # noqa: E402

SAMPLE = os.path.join(HERE, "sample")
PULL_2 = os.path.join(SAMPLE, "pull_2026-09")


def rules():
    return crossref.load_rules()


def load_pull(directory):
    return (
        crossref.load_records(os.path.join(directory, "estate_cases.jsonl")),
        crossref.load_records(os.path.join(directory, "parcels.jsonl")),
        crossref.load_records(os.path.join(directory, "deeds.jsonl")),
    )


def load(conn, directory, as_of):
    estates, parcels, deeds = load_pull(directory)
    result = crossref.crossref(estates, parcels, deeds, rules())
    return store.load_run(conn, result, estates, parcels, deeds, rules(), as_of=as_of)


def status_of(conn, left, right):
    row = conn.execute("SELECT status FROM entity_match WHERE left_id = ? AND right_id = ?", (left, right)).fetchone()
    return row["status"] if row else None


JOHN = "MECKLENBURG/26 E 001234"
MARY = "MECKLENBURG/26 E 001235"
ROBERT = "MECKLENBURG/26 E 001236"
DAVID = "MECKLENBURG/26 E 001237"
HELEN = "MECKLENBURG/26 E 004102"


class FirstPull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(":memory:")
        cls.run_id, cls.delta = load(cls.conn, SAMPLE, "2026-04-15")

    def test_every_candidate_is_recorded_verbatim(self):
        self.assertEqual(self.conn.execute("SELECT count(*) FROM match_observation WHERE run_id = ?", (self.run_id,)).fetchone()[0], 14)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM entity_match").fetchone()[0], 14)

    def test_first_run_is_all_new(self):
        self.assertEqual(self.delta["new_confirmed"], [(JOHN, "MECKLENBURG/045-121-08"), (MARY, "MECKLENBURG/213-002-44")])
        self.assertEqual(len(self.delta["new_pending"]), 3)
        self.assertEqual(self.delta["new_rejected"], 8)
        self.assertEqual(self.delta["changed"], [])

    def test_post_death_executor_deed_already_in_the_pull_transfers_the_lead(self):
        # crossref alone confirms this pair with a post_death_conveyance flag and
        # leaves it on the lead list; the store takes it off.
        self.assertEqual(status_of(self.conn, ROBERT, "MECKLENBURG/017-455-02"), "transferred")
        kinds = [(e["kind"], e["left_id"]) for e in self.delta["transfers"]]
        self.assertEqual(kinds, [("deed_from_estate", ROBERT)])
        self.assertEqual(self.delta["transfers"][0]["detail"]["grantee_relation"], "personal_representative")

    def test_only_tracked_parcels_are_snapshotted(self):
        snapped = {r["right_id"] for r in self.conn.execute("SELECT right_id FROM parcel_snapshot WHERE run_id = ?", (self.run_id,))}
        self.assertIn("MECKLENBURG/045-121-08", snapped)  # confirmed
        self.assertIn("MECKLENBURG/099-001-01", snapped)  # pending
        self.assertNotIn("MECKLENBURG/099-001-02", snapped)  # rejected
        self.assertNotIn("MECKLENBURG/133-070-13", snapped)  # never a candidate

    def test_lead_list_excludes_the_transferred_pair(self):
        self.assertEqual([r["right_id"] for r in report.leads(self.conn)], ["MECKLENBURG/213-002-44", "MECKLENBURG/045-121-08"])

    def test_daily_cohort_counts_the_day_it_arrived_and_the_day_it_left(self):
        rows = report.daily(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            {k: rows[0][k] for k in ("day", "new_confirmed", "new_pending", "transferred", "active_confirmed")},
            {"day": "2026-04-15", "new_confirmed": 3, "new_pending": 3, "transferred": 1, "active_confirmed": 2},
        )


class SecondPull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = store.connect(":memory:")
        load(cls.conn, SAMPLE, "2026-04-15")
        cls.run_id, cls.delta = load(cls.conn, PULL_2, "2026-09-01")

    def test_only_the_new_estate_is_new(self):
        self.assertEqual(self.delta["new_confirmed"], [(HELEN, "MECKLENBURG/700-100-01")])
        self.assertEqual(self.delta["new_pending"], [])

    def test_administrator_deed_and_owner_change_both_detected_and_deduplicated(self):
        kinds = sorted((e["kind"], e["right_id"]) for e in self.delta["transfers"])
        self.assertEqual(
            kinds,
            [
                ("deed_from_estate", "MECKLENBURG/099-001-01"),
                ("deed_from_estate", "MECKLENBURG/213-002-44"),
                ("owner_changed", "MECKLENBURG/099-001-01"),
                ("owner_changed", "MECKLENBURG/213-002-44"),
                ("sale_date_advanced", "MECKLENBURG/099-001-01"),
                ("sale_date_advanced", "MECKLENBURG/213-002-44"),
            ],
        )
        # Robert's executor deed was already on file from run 1: not re-fired.
        self.assertEqual(self.conn.execute("SELECT count(*) FROM parcel_transfer WHERE left_id = ?", (ROBERT,)).fetchone()[0], 1)

    def test_status_changes_are_reported(self):
        changed = {(c["left_id"], c["right_id"]): (c["before"], c["after"]) for c in self.delta["changed"]}
        self.assertEqual(changed[(MARY, "MECKLENBURG/213-002-44")], ("confirmed", "transferred"))
        self.assertEqual(changed[(DAVID, "MECKLENBURG/099-001-01")], ("pending", "transferred"))

    def test_transferred_is_sticky_even_if_the_rules_would_reconfirm(self):
        self.assertEqual(status_of(self.conn, ROBERT, "MECKLENBURG/017-455-02"), "transferred")
        row = self.conn.execute("SELECT transferred_as_of FROM entity_match WHERE left_id = ?", (ROBERT,)).fetchone()
        self.assertEqual(row["transferred_as_of"], "2026-04-15")

    def test_lead_list_is_what_is_left(self):
        self.assertEqual([r["right_id"] for r in report.leads(self.conn)], ["MECKLENBURG/700-100-01", "MECKLENBURG/045-121-08"])

    def test_daily_cohorts_by_pull_date(self):
        rows = {r["day"]: r for r in report.daily(self.conn)}
        self.assertEqual((rows["2026-04-15"]["new_confirmed"], rows["2026-04-15"]["transferred"]), (3, 1))
        self.assertEqual((rows["2026-09-01"]["new_confirmed"], rows["2026-09-01"]["transferred"], rows["2026-09-01"]["active_confirmed"]), (1, 2, 1))

    def test_daily_cohorts_by_filing_date_look_back_before_the_store_existed(self):
        rows = {r["day"]: r for r in report.daily(self.conn, by="filing_date")}
        self.assertEqual(rows["2026-01-22"]["new_confirmed"], 1)  # Robert, filed in January
        self.assertEqual(rows["2026-08-20"]["new_confirmed"], 1)  # Helen

    def test_outcomes_credit_the_evidence_that_held_up(self):
        summary = report.outcomes(self.conn)
        self.assertEqual(summary["labeled"], 3)
        by_evidence = {row["name"]: row for row in summary["by_evidence"]}
        self.assertEqual((by_evidence["deed_grantor_link"]["tp"], by_evidence["deed_grantor_link"]["fp"]), (1, 0))
        self.assertEqual(by_evidence["estate_marker_on_owner"]["tp"], 1)

    def test_transfer_detail_is_json_and_names_the_instrument(self):
        events = report.transfer_events(self.conn)
        deed_events = [e for e in events if e["kind"] == "deed_from_estate"]
        self.assertEqual(len(deed_events), 3)
        self.assertTrue(all(e["detail"]["instrument"].startswith("MECKLENBURG/2026") for e in deed_events))
        self.assertIsInstance(json.loads(self.conn.execute("SELECT detail FROM parcel_transfer LIMIT 1").fetchone()["detail"]), dict)

    def test_report_formatters_render(self):
        self.assertIn("NEW TARGETS BY PULL DATE", report.format_daily(report.daily(self.conn), "as_of"))
        self.assertIn("LEADS", report.format_leads(report.leads(self.conn)))
        self.assertIn("ADMINISTRATOR DEED", report.format_transfers(report.transfer_events(self.conn)))
        self.assertIn("BY EVIDENCE", report.format_outcomes(report.outcomes(self.conn)))
        self.assertIn("TRANSFERS DETECTED", store.format_delta(self.delta))


class OwnerStringChanges(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.estates = [
            {
                "county": "MECKLENBURG",
                "file_number": "26 E 1",
                "decedent_name": "John Q Public",
                "date_of_death": "2026-01-14",
                "filing_date": "2026-01-28",
                "personal_rep_name": "Jane R Public",
                "pr_mailing_address": "4210 Elm St Charlotte NC 28205",
            }
        ]
        self.parcel = {
            "county": "MECKLENBURG",
            "pin": "1",
            "situs_address": "4210 Elm St Charlotte NC 28205",
            "owner_name": "PUBLIC JOHN Q",
            "owner_mailing_address": "4210 ELM ST CHARLOTTE NC 28205",
            "assessed_value": 1,
        }

    def run_with(self, owner, as_of, **extra):
        parcel = dict(self.parcel, owner_name=owner, **extra)
        result = crossref.crossref(self.estates, [parcel], [], rules())
        return store.load_run(self.conn, result, self.estates, [parcel], [], rules(), as_of=as_of)[1]

    def test_retitling_to_the_estate_is_not_a_transfer(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        delta = self.run_with("PUBLIC JOHN Q ESTATE OF", "2026-03-01")
        self.assertEqual([e["kind"] for e in delta["transfers"]], ["owner_restyled"])
        self.assertEqual(status_of(self.conn, "MECKLENBURG/26 E 1", "MECKLENBURG/1"), "confirmed")

    def test_owner_losing_the_decedent_is_a_transfer_even_with_no_deed_on_file(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        delta = self.run_with("NEWOWNER SARA", "2026-03-01")
        self.assertEqual([e["kind"] for e in delta["transfers"]], ["owner_changed"])
        self.assertEqual(status_of(self.conn, "MECKLENBURG/26 E 1", "MECKLENBURG/1"), "transferred")
        self.assertEqual(delta["unobserved"], [])  # transferred pairs are no longer tracked

    def test_a_pair_that_stops_being_a_candidate_is_still_watched(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        # Owner string no longer blocks on the decedent, so crossref produces no
        # candidate -- but the parcel is still snapshotted and the change seen.
        delta = self.run_with("NEWOWNER SARA", "2026-03-01")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM parcel_snapshot").fetchone()[0], 2)
        self.assertEqual(delta["transfers"][0]["detail"]["owner_after"], "NEWOWNER SARA")

    def test_sale_date_after_death_is_evidence_but_not_a_verdict(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        delta = self.run_with("PUBLIC JOHN Q", "2026-03-01", last_sale_date="2026-02-20")
        self.assertEqual([e["kind"] for e in delta["transfers"]], ["sale_date_advanced"])
        self.assertEqual(status_of(self.conn, "MECKLENBURG/26 E 1", "MECKLENBURG/1"), "confirmed")

    def test_namesake_deed_sends_a_confirmed_pair_back_to_review(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        deed = {
            "county": "MECKLENBURG",
            "instrument_number": "2026-9",
            "recorded_date": "2026-02-20",
            "instrument_type": "WD",
            "grantor_name": "PUBLIC JOHN Q",
            "grantee_name": "BUYER BOB",
            "parcel_pin": "1",
        }
        result = crossref.crossref(self.estates, [self.parcel], [deed], rules())
        delta = store.load_run(self.conn, result, self.estates, [self.parcel], [deed], rules(), as_of="2026-03-01")[1]
        self.assertEqual([e["kind"] for e in delta["transfers"]], ["namesake_conveyance"])
        self.assertEqual(status_of(self.conn, "MECKLENBURG/26 E 1", "MECKLENBURG/1"), "pending")

    def test_reloading_the_same_pull_changes_nothing(self):
        self.run_with("PUBLIC JOHN Q", "2026-02-01")
        delta = self.run_with("PUBLIC JOHN Q", "2026-02-01")
        self.assertEqual((delta["new_confirmed"], delta["changed"], delta["transfers"]), ([], [], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
