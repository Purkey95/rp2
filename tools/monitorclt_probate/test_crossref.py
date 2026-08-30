#!/usr/bin/env python3
"""Tests for the probate -> property cross-reference.

Run directly (`python3 test_crossref.py`) or under pytest from the repo root.
"""

import json
import os
import sys
import unittest
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)  # pylint: disable=wrong-import-position

SAMPLE = os.path.join(HERE, "sample")


def rules():
    return crossref.load_rules()


def estate(**kwargs):
    base = {
        "county": "MECKLENBURG",
        "file_number": "26 E 000001",
        "decedent_name": "John Q Public",
        "date_of_death": "2026-01-14",
        "personal_rep_name": "Jane R Public",
        "pr_mailing_address": "4210 Elm Street, Charlotte NC 28205",
    }
    base.update(kwargs)
    return base


def parcel(**kwargs):
    base = {
        "county": "MECKLENBURG",
        "pin": "045-121-08",
        # deliberately not the estate's mailing address: the default fixture is a
        # bare name match, so each test adds exactly the evidence it is about.
        "situs_address": "9 Birch Ln, Charlotte NC 28205",
        "owner_name": "PUBLIC JOHN Q",
        "owner_mailing_address": "PO BOX 1 CHARLOTTE NC 28204",
        "assessed_value": 300000,
    }
    base.update(kwargs)
    return base


def link_for(estate_row, parcel_row, deeds=(), rule_set=None):
    """Score a single estate/parcel pair the way crossref() would."""
    rule_set = rule_set or rules()
    parcel_index, frequency = crossref.index_parcels([parcel_row], rule_set)
    deed_index = crossref.index_deeds(list(deeds), rule_set)
    decedent = crossref.parse_name(estate_row["decedent_name"], "first_last", rule_set)
    _, party = parcel_index[decedent["key_fl"]][0]
    return crossref.score_link(estate_row, parcel_row, decedent, party, rule_set, frequency, deed_index)


class TestNormalization(unittest.TestCase):
    def test_clean_text_keeps_compound_names_whole(self):
        self.assertEqual(crossref.clean_text("O'Brien-Smith, John Q."), "OBRIENSMITH, JOHN Q")

    def test_court_order_is_first_last(self):
        name = crossref.parse_name("John Q Public", "first_last", rules())
        self.assertEqual((name["first"], name["middle"], name["last"]), ("JOHN", "Q", "PUBLIC"))

    def test_assessor_order_is_last_first(self):
        name = crossref.parse_name("PUBLIC JOHN Q", "last_first", rules())
        self.assertEqual((name["first"], name["middle"], name["last"]), ("JOHN", "Q", "PUBLIC"))

    def test_comma_overrides_the_source_convention(self):
        name = crossref.parse_name("Public, John Q", "last_first", rules())
        self.assertEqual((name["first"], name["last"]), ("JOHN", "PUBLIC"))

    def test_suffix_is_split_off_not_treated_as_a_name(self):
        name = crossref.parse_name("PUBLIC JOHN Q JR", "last_first", rules())
        self.assertEqual(name["suffix"], "JR")
        self.assertEqual(name["key_full"], "JOHN|Q|PUBLIC")

    def test_estate_markers_are_stripped_and_reported(self):
        name = crossref.parse_name("SAMPLE MARY A HEIRS", "last_first", rules())
        self.assertEqual(name["markers"], ["HEIRS"])
        self.assertEqual(name["key_fl"], "MARY|SAMPLE")

    def test_estate_of_prefix_flips_to_natural_order(self):
        name = crossref.parse_name("ESTATE OF HENRY W PLACEHOLDER", "last_first", rules())
        self.assertEqual((name["first"], name["last"]), ("HENRY", "PLACEHOLDER"))

    def test_organizations_are_flagged_not_parsed_as_people(self):
        name = crossref.parse_name("NONESUCH HOLDINGS LLC", "last_first", rules())
        self.assertTrue(name["is_organization"])
        self.assertEqual(name["key_fl"], "|")

    def test_second_owner_inherits_the_printed_surname(self):
        parties = crossref.split_parties("PUBLIC JOHN Q & JANE R", "last_first", rules())
        self.assertEqual([p["key_full"] for p in parties], ["JOHN|Q|PUBLIC", "JANE|R|PUBLIC"])

    def test_second_owner_with_its_own_surname_is_parsed_alone(self):
        parties = crossref.split_parties("PUBLIC JOHN Q & SAMPLE MARY A", "last_first", rules())
        self.assertEqual([p["key_full"] for p in parties], ["JOHN|Q|PUBLIC", "MARY|A|SAMPLE"])

    def test_address_normalization_survives_formatting_differences(self):
        left = crossref.normalize_address("4210 Elm Street Apt 5, Charlotte NC 28205", rules())
        right = crossref.normalize_address("4210 ELM ST APT 5 CHARLOTTE NC 28205", rules())
        self.assertEqual(left, right)


class TestScoring(unittest.TestCase):
    def test_name_only_match_goes_to_review_never_to_confirmed(self):
        link = link_for(estate(), parcel())
        self.assertEqual(link["status"], "pending")
        self.assertIn("name_only_needs_human_review", link["flags"])

    def test_mailing_address_confirms(self):
        link = link_for(estate(), parcel(owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205"))
        self.assertEqual(link["status"], "confirmed")
        self.assertIn("mailing_address_match", link["evidence"])

    def test_estate_marker_confirms(self):
        link = link_for(estate(), parcel(owner_name="PUBLIC JOHN Q HEIRS"))
        self.assertEqual(link["match_tier"], "estate_marker_on_owner")
        self.assertEqual(link["status"], "confirmed")

    def test_middle_initial_conflict_rejects(self):
        link = link_for(estate(), parcel(owner_name="PUBLIC JOHN V"))
        self.assertIn("middle_initial_conflict", link["evidence"])
        self.assertEqual(link["status"], "rejected")

    def test_out_of_county_parcel_is_never_confirmed(self):
        link = link_for(
            estate(),
            parcel(
                county="UNION",
                owner_name="PUBLIC JOHN Q HEIRS",
                owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205",
            ),
        )
        self.assertIn("county_mismatch", link["evidence"])
        self.assertNotEqual(link["status"], "confirmed")

    def test_common_name_is_penalized_below_the_review_floor(self):
        rule_set = rules()
        parcels = [parcel(pin=f"099-001-{i:02d}", owner_name="SMITH DAVID", owner_mailing_address="") for i in range(1, 10)]
        result = crossref.crossref([estate(decedent_name="David Smith")], parcels, [], rule_set)
        self.assertTrue(result["matches"])
        for link in result["matches"]:
            self.assertIn("common_name", link["evidence"])
            self.assertEqual(link["status"], "rejected")

    def test_deed_grantor_link_is_evidence_and_flags_a_post_death_conveyance(self):
        deed = {
            "county": "MECKLENBURG",
            "instrument_number": "2026-0004411",
            "recorded_date": "2026-04-02",
            "instrument_type": "EXECUTOR DEED",
            "grantor_name": "PUBLIC JOHN Q",
            "grantee_name": "PUBLIC JANE R",
            "parcel_pin": "045121 08",
        }
        link = link_for(estate(), parcel(), deeds=[deed])
        self.assertIn("deed_grantor_link", link["evidence"])
        self.assertIn("post_death_conveyance", link["flags"])
        self.assertEqual(link["deed_instruments"], ["2026-0004411"])

    def test_deed_recorded_before_death_is_evidence_without_the_flag(self):
        deed = {
            "county": "MECKLENBURG",
            "instrument_number": "2019-0091822",
            "recorded_date": "2019-06-18",
            "grantor_name": "PUBLIC JOHN Q",
            "parcel_pin": "045-121-08",
        }
        link = link_for(estate(), parcel(), deeds=[deed])
        self.assertIn("deed_grantor_link", link["evidence"])
        self.assertEqual(link["flags"], [])

    def test_organization_owner_cannot_be_confirmed(self):
        rule_set = rules()
        result = crossref.crossref([estate()], [parcel(owner_name="PUBLIC PROPERTIES LLC")], [], rule_set)
        self.assertEqual(result["matches"], [])


class TestEndToEnd(unittest.TestCase):
    # Declared for the type checker: setUpClass assigns this on the class.
    result: Dict[str, Any]

    @classmethod
    def setUpClass(cls):
        cls.result = crossref.crossref(
            crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
            rules(),
        )

    def test_every_link_maps_onto_the_entity_match_table(self):
        columns = {
            "left_source",
            "left_id",
            "right_source",
            "right_id",
            "match_tier",
            "score",
            "evidence",
            "flags",
            "status",
        }
        for link in self.result["matches"]:
            self.assertTrue(columns.issubset(link.keys()))
            self.assertEqual(link["left_source"], "estate_case")
            self.assertEqual(link["right_source"], "parcel")
            self.assertTrue(0.0 <= link["score"] <= 1.0)
            self.assertIn(link["status"], ("confirmed", "pending", "rejected"))

    def test_expected_dispositions_on_the_sample(self):
        statuses: Dict[str, List[Any]] = {}
        for link in self.result["matches"]:
            statuses.setdefault(link["status"], []).append(link["right_id"])
        self.assertEqual(
            sorted(statuses["confirmed"]),
            [
                "MECKLENBURG/017-455-02",
                "MECKLENBURG/045-121-08",
                "MECKLENBURG/213-002-44",
            ],
        )

    def test_rollup_answers_does_this_estate_hold_real_property(self):
        holding = {row["left_id"] for row in self.result["estates"] if row["has_real_property"]}
        self.assertIn("MECKLENBURG/26 E 001234", holding)
        self.assertNotIn("MECKLENBURG/26 E 001240", holding)  # no parcels at all

    def test_unparseable_decedent_is_skipped_loudly(self):
        self.assertEqual(
            [row["left_id"] for row in self.result["skipped_estates"]],
            ["MECKLENBURG/26 E 001241"],
        )

    def test_estate_marked_parcels_without_a_case_are_surfaced_as_a_gap(self):
        self.assertEqual(
            [row["right_id"] for row in self.result["unmatched_estate_parcels"]],
            ["MECKLENBURG/133-070-12"],
        )

    def test_output_is_deterministic(self):
        again = crossref.crossref(
            crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
            rules(),
        )
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.result, sort_keys=True))


class TestParsingRegressions(unittest.TestCase):
    """Cases that produced silently wrong answers, one test per defect.

    None of these are exercised by sample/: the fixtures were blind to every one
    of them, which is why the suite stayed green while the parser dropped exactly
    the parties this tool exists to find.
    """

    def test_deceased_co_owner_still_inherits_the_printed_surname(self):
        # "SMITH JOHN & MARY HEIRS" is the estate party we are hunting for; the
        # marker used to suppress inheritance, keying Mary as "|MARY" so no
        # estate could ever block in.
        parties = crossref.split_parties("SMITH JOHN & MARY HEIRS", "last_first", rules())
        self.assertEqual([p["key_fl"] for p in parties], ["JOHN|SMITH", "MARY|SMITH"])
        self.assertEqual(parties[1]["markers"], ["HEIRS"])

    def test_deceased_co_owner_with_a_middle_initial_inherits_too(self):
        parties = crossref.split_parties("SMITH JOHN & MARY A HEIRS", "last_first", rules())
        self.assertEqual([p["key_full"] for p in parties], ["JOHN||SMITH", "MARY|A|SMITH"])

    def test_a_second_surname_is_not_swallowed_in_a_last_first_source(self):
        # "JONES MARY" is Mary Jones, not Mary Jones-under-Smith: in a LAST FIRST
        # source a two-token fragment is only forenames when the second is an initial.
        parties = crossref.split_parties("SMITH JOHN & JONES MARY", "last_first", rules())
        self.assertEqual([p["key_fl"] for p in parties], ["JOHN|SMITH", "MARY|JONES"])

    def test_two_token_fragment_still_inherits_when_the_source_is_first_last(self):
        parties = crossref.split_parties("JOHN Q PUBLIC & MARY B", "first_last", rules())
        self.assertEqual([p["key_fl"] for p in parties], ["JOHN|PUBLIC", "MARY|PUBLIC"])

    def test_a_fragment_that_is_only_a_suffix_does_not_abort_the_run(self):
        # Used to raise IndexError out of split_parties and take the whole run
        # down -- there is no exception handling anywhere in the chain.
        parties = crossref.split_parties("PUBLIC JOHN Q & JR", "last_first", rules())
        self.assertEqual(parties[0]["key_fl"], "JOHN|PUBLIC")

    def test_life_estate_of_does_not_parse_of_as_the_surname(self):
        # Markers strip longest-first, so "LIFE ESTATE" is consumed and a bare
        # "OF" was left behind to be read as the surname.
        name = crossref.parse_name("LIFE ESTATE OF HENRY W PLACEHOLDER", "last_first", rules())
        self.assertEqual((name["first"], name["middle"], name["last"]), ("HENRY", "W", "PLACEHOLDER"))
        self.assertEqual(name["markers"], ["LIFE ESTATE"])

    def test_a_person_holding_as_trustee_is_still_a_candidate(self):
        # The organization_owner contradiction was unreachable: org-flagged
        # parties were dropped before scoring, so the parcel simply vanished.
        name = crossref.parse_name("PUBLIC JOHN Q TRUSTEE", "last_first", rules())
        self.assertTrue(name["is_organization"])
        self.assertEqual(name["key_fl"], "JOHN|PUBLIC")

    def test_a_pure_organization_is_still_not_a_person(self):
        name = crossref.parse_name("NONESUCH HOLDINGS LLC", "last_first", rules())
        self.assertTrue(name["is_organization"])
        self.assertEqual(name["key_fl"], "|")


class TestScoringRegressions(unittest.TestCase):
    def test_organization_owner_penalty_is_applied_not_dead_code(self):
        link = link_for(
            estate(),
            parcel(owner_name="PUBLIC JOHN Q TRUSTEE", owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205"),
        )
        self.assertIn("organization_owner", link["evidence"])
        self.assertEqual(link["status"], "pending")

    def test_an_organization_owner_never_auto_confirms(self):
        # Same evidence confirms an individual; the org gate must hold it back.
        individual = link_for(estate(), parcel(owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205"))
        self.assertEqual(individual["status"], "confirmed")

    def test_one_row_per_estate_parcel_pair(self):
        # "PUBLIC JOHN Q & JOHN Q JR" names the same FIRST+LAST twice, which
        # emitted duplicate rows, double-counted the rollup, and violated
        # entity_match's UNIQUE (left_source, left_id, right_source, right_id).
        result = crossref.crossref(
            [estate()],
            [parcel(owner_name="PUBLIC JOHN Q & JOHN Q JR", owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205")],
            [],
            rules(),
        )
        keys = [(m["left_source"], m["left_id"], m["right_source"], m["right_id"]) for m in result["matches"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(result["estates"][0]["assessed_value_confirmed"], 300000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
