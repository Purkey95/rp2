#!/usr/bin/env python3
"""Tests for the deed-versus-estate reading shared by monitoring and the backtest.

Run directly (`python3 test_transfers.py`) or under pytest from the repo root.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)
import transfers  # noqa: E402


def rules():
    return crossref.load_rules()


def estate(**kwargs):
    base = {
        "county": "MECKLENBURG",
        "file_number": "26 E 000001",
        "decedent_name": "David Smith",
        "date_of_death": "2026-03-11",
        "filing_date": "2026-03-30",
        "personal_rep_name": "Ellen Smith",
    }
    base.update(kwargs)
    return base


def deed(**kwargs):
    base = {
        "county": "MECKLENBURG",
        "instrument_number": "2026-0000001",
        "recorded_date": "2026-08-01",
        "instrument_type": "WD",
        "grantor_name": "SMITH DAVID",
        "grantee_name": "BUYER BOB",
        "parcel_pin": "099-001-01",
    }
    base.update(kwargs)
    return base


class ConveyanceReading(unittest.TestCase):
    def test_deed_before_death_says_nothing(self):
        self.assertIsNone(transfers.classify_conveyance(deed(recorded_date="2026-01-01"), estate(), rules()))

    def test_filing_date_stands_in_when_date_of_death_is_missing(self):
        reading = transfers.classify_conveyance(deed(recorded_date="2026-03-31"), estate(date_of_death=None), rules())
        self.assertEqual(reading["relation"], "namesake")
        self.assertIsNone(transfers.classify_conveyance(deed(recorded_date="2026-03-29"), estate(date_of_death=None), rules()))

    def test_personal_representative_as_grantor_is_the_estate_conveying(self):
        reading = transfers.classify_conveyance(deed(grantor_name="SMITH ELLEN ADMINISTRATOR", instrument_type="ADMINISTRATOR DEED"), estate(), rules())
        self.assertEqual((reading["relation"], reading["basis"]), ("estate", "grantor_is_personal_representative"))

    def test_estate_marker_on_decedent_grantor(self):
        reading = transfers.classify_conveyance(deed(grantor_name="SMITH DAVID ESTATE OF"), estate(), rules())
        self.assertEqual((reading["relation"], reading["basis"]), ("estate", "estate_marker_on_decedent_grantor"))

    def test_fiduciary_instrument_from_decedent_name(self):
        reading = transfers.classify_conveyance(deed(instrument_type="EXECUTOR DEED"), estate(), rules())
        self.assertEqual((reading["relation"], reading["basis"]), ("estate", "fiduciary_instrument_from_decedent"))

    def test_plain_deed_in_bare_decedent_name_after_death_is_a_namesake(self):
        reading = transfers.classify_conveyance(deed(), estate(), rules())
        self.assertEqual((reading["relation"], reading["basis"]), ("namesake", "post_death_deed_in_bare_decedent_name"))

    def test_middle_initial_conflict_is_surfaced_not_acted_on(self):
        reading = transfers.classify_conveyance(
            deed(grantor_name="SMITH DAVID R", instrument_type="EXECUTOR DEED"), estate(decedent_name="David Q Smith"), rules()
        )
        self.assertEqual((reading["relation"], reading["basis"]), ("ambiguous", "middle_initial_conflict"))

    def test_unrelated_grantor_says_nothing(self):
        self.assertIsNone(transfers.classify_conveyance(deed(grantor_name="JONES LINDA"), estate(), rules()))

    def test_organization_grantor_never_reads_as_the_decedent(self):
        self.assertIsNone(transfers.classify_conveyance(deed(grantor_name="SMITH DAVID PROPERTIES LLC", instrument_type="EXECUTOR DEED"), estate(), rules()))

    def test_semicolon_separated_grantors_are_each_read(self):
        reading = transfers.classify_conveyance(
            deed(grantor_name="SMITH DAVID ESTATE OF; SMITH ELLEN EXECUTRIX", instrument_type="EXECUTOR DEED"), estate(), rules()
        )
        self.assertEqual(reading["relation"], "estate")


class GranteeReading(unittest.TestCase):
    def read(self, grantee):
        return transfers.classify_conveyance(deed(instrument_type="EXECUTOR DEED", grantee_name=grantee), estate(), rules())["grantee_relation"]

    def test_to_the_representative(self):
        self.assertEqual(self.read("SMITH ELLEN"), "personal_representative")

    def test_within_the_family_name(self):
        self.assertEqual(self.read("SMITH KAREN & SMITH PAUL"), "shares_decedent_surname")

    def test_to_an_organization(self):
        self.assertEqual(self.read("BUYER INVESTMENTS LLC"), "organization")

    def test_to_a_third_party(self):
        self.assertEqual(self.read("BUYER BOB"), "third_party")

    def test_missing_grantee(self):
        self.assertEqual(self.read(""), "unknown")


class DeedKeys(unittest.TestCase):
    def test_instrument_number_wins(self):
        self.assertEqual(transfers.deed_key(deed(book="1", page="2")), "MECKLENBURG/20260000001")

    def test_book_page_fallback(self):
        self.assertEqual(transfers.deed_key(deed(instrument_number=None, book="38221", page="0417")), "MECKLENBURG/38221/0417")

    def test_parcel_key_normalizes_like_the_matcher(self):
        self.assertEqual(transfers.deed_parcel_key(deed(parcel_pin="099-001-01")), crossref.pin_key("MECKLENBURG", "09900101"))
        self.assertIsNone(transfers.deed_parcel_key(deed(parcel_pin=None)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
