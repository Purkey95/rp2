#!/usr/bin/env python3
"""Tests for the probate -> property cross-reference.

Run directly (`python3 test_crossref.py`) or under pytest from the repo root.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)

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


def entity(**kwargs):
    base = {
        "sos_id": "1234567",
        "entity_name": "Nonesuch Holdings, LLC",
        "entity_type": "Limited Liability Company",
        "status": "Current-Active",
        "principal_office_address": "1 Office Park, Charlotte NC 28202",
        "mailing_address": None,
        "registered_agent_name": "Placeholder Registered Agents Inc",
        "registered_agent_address": "1 Agent Row, Raleigh NC 27601",
        "officials": [{"person_name": "John Q Public", "title": "Manager", "source": "AR 2025"}],
    }
    base.update(kwargs)
    return base


def org_parcel(**kwargs):
    base = parcel(
        pin="133-070-13",
        owner_name="NONESUCH HOLDINGS LLC",
        owner_mailing_address="PO BOX 12 CHARLOTTE NC 28201",
        situs_address="79 Hypothetical Cir, Charlotte NC 28211",
    )
    base.update(kwargs)
    return base


def entity_links(estate_row, parcels, entities, deeds=(), rule_set=None):
    """Every link crossref() produces for one estate against these parcels + entities."""
    result = crossref.crossref([estate_row], parcels, list(deeds), rule_set or rules(), entities=entities)
    return result["matches"]


def link_for(estate_row, parcel_row, deeds=(), rule_set=None):
    """Score a single estate/parcel pair the way crossref() would."""
    rule_set = rule_set or rules()
    parcel_index, frequency = crossref.index_parcels([parcel_row], rule_set)
    deed_index = crossref.index_deeds(list(deeds), rule_set)
    decedent = crossref.parse_name(estate_row["decedent_name"], "first_last", rule_set)
    _, party = parcel_index[decedent["key_fl"]][0]
    return crossref.score_link(
        estate_row, parcel_row, decedent, party, rule_set, frequency, deed_index
    )


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

    def test_trustee_role_is_stripped_and_recorded(self):
        name = crossref.parse_name("PUBLIC JOHN Q TRUSTEE", "last_first", rules())
        self.assertFalse(name["is_organization"])
        self.assertEqual(name["key_full"], "JOHN|Q|PUBLIC")
        self.assertEqual(name["roles"], ["TRUSTEE"])

    def test_ttee_abbreviation_is_a_role(self):
        name = crossref.parse_name("PUBLIC JOHN Q TTEE", "last_first", rules())
        self.assertEqual((name["roles"], name["key_fl"]), (["TTEE"], "JOHN|PUBLIC"))

    def test_family_trust_stays_an_organization(self):
        for raw in ("PUBLIC FAMILY TRUST", "PUBLIC JOHN Q TRUSTEE OF THE PUBLIC FAMILY TRUST"):
            self.assertTrue(crossref.parse_name(raw, "last_first", rules())["is_organization"], raw)

    def test_co_trustees_share_the_role_and_the_surname(self):
        parties = crossref.split_parties("PUBLIC JOHN Q & JANE R TRUSTEES", "last_first", rules())
        self.assertEqual([p["key_full"] for p in parties], ["JOHN|Q|PUBLIC", "JANE|R|PUBLIC"])
        self.assertEqual([p["roles"] for p in parties], [["TRUSTEES"], ["TRUSTEES"]])

    def test_entity_name_normalization_strips_suffix_noise(self):
        left = crossref.normalize_entity_name("Nonesuch Holdings, L.L.C.", rules())
        right = crossref.normalize_entity_name("THE NONESUCH HOLDINGS LLC", rules())
        self.assertEqual(left, ("NONESUCH HOLDINGS", "LLC"))
        self.assertEqual(left[0], right[0])

    def test_entity_name_normalization_keeps_distinguishing_words(self):
        a = crossref.normalize_entity_name("SMITH PROPERTIES LLC", rules())[0]
        b = crossref.normalize_entity_name("SMITH PROPERTY LLC", rules())[0]
        self.assertNotEqual(a, b)

    def test_entity_key_is_empty_for_a_bare_suffix(self):
        self.assertEqual(crossref.normalize_entity_name("LLC", rules()), ("", "LLC"))

    def test_entity_suffix_family_is_recorded_on_the_party(self):
        name = crossref.parse_name("NONESUCH HOLDINGS INC", "last_first", rules())
        self.assertEqual((name["entity_key"], name["entity_family"]), ("NONESUCH HOLDINGS", "CORP"))

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
        link = link_for(
            estate(), parcel(owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205")
        )
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
        parcels = [
            parcel(pin="099-001-{0:02d}".format(i), owner_name="SMITH DAVID", owner_mailing_address="")
            for i in range(1, 10)
        ]
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

    def test_trustee_held_parcel_caps_at_pending(self):
        link = link_for(
            estate(),
            parcel(owner_name="PUBLIC JOHN Q TRUSTEE", owner_mailing_address="4210 ELM ST CHARLOTTE NC 28205"),
        )
        self.assertEqual(link["score"], 0.85)  # would auto-confirm on the direct path
        self.assertEqual(link["status"], "pending")
        self.assertEqual(link["flags"], ["held_in_trust"])
        self.assertNotIn("name_only_needs_human_review", link["flags"])

    def test_cap_flags_are_derived_from_evidence_not_flags(self):
        self.assertEqual(crossref.cap_flags(["name_full_exact", "trustee_owner"]), ["held_in_trust"])
        self.assertEqual(crossref.cap_flags(["name_full_exact", "mailing_address_match"]), [])

    def test_capped_link_below_the_review_floor_is_still_rejected(self):
        link = link_for(estate(), parcel(owner_name="PUBLIC JOHN V TRUSTEE"))
        self.assertEqual(link["status"], "rejected")

    def test_organization_owner_cannot_be_confirmed(self):
        rule_set = rules()
        result = crossref.crossref(
            [estate()], [parcel(owner_name="PUBLIC PROPERTIES LLC")], [], rule_set
        )
        self.assertEqual(result["matches"], [])


class TestEntityPath(unittest.TestCase):
    def test_official_link_reaches_the_parcel_through_the_entity(self):
        links = entity_links(estate(), [org_parcel()], [entity()])
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual((link["via_source"], link["via_id"]), ("business_entity", "1234567"))
        self.assertIn("entity_official_link", link["evidence"])
        self.assertEqual(link["status"], "pending")
        self.assertIn("held_via_entity", link["flags"])
        self.assertEqual(link["via_entity"]["title"], "Manager")
        self.assertEqual(link["right_id"], "MECKLENBURG/133-070-13")

    def test_entity_link_caps_at_pending_with_max_evidence(self):
        ent = entity(
            principal_office_address="4210 Elm Street, Charlotte NC 28205",  # = estate PR address
            mailing_address="PO Box 12, Charlotte NC 28201",  # = parcel mailing
        )
        link = entity_links(estate(), [org_parcel()], [ent])[0]
        self.assertIn("entity_office_address_match", link["evidence"])
        self.assertIn("entity_address_on_parcel", link["evidence"])
        self.assertEqual(link["score"], 1.0)
        self.assertEqual(link["status"], "pending")
        self.assertEqual(link["flags"], ["held_via_entity"])

    def test_entity_link_cannot_be_confirmed_under_any_rules(self):
        rule_set = rules()
        rule_set["thresholds"]["auto_confirm"] = 0.0
        rule_set["require_corroboration_to_confirm"] = False
        ent = entity(principal_office_address="4210 Elm Street, Charlotte NC 28205")
        link = entity_links(estate(), [org_parcel()], [ent], rule_set=rule_set)[0]
        self.assertEqual(link["status"], "pending")

    def test_organization_owner_penalty_does_not_apply_on_the_entity_path(self):
        link = entity_links(estate(), [org_parcel()], [entity()])[0]
        self.assertNotIn("organization_owner", link["evidence"])

    def test_registered_agent_is_weaker_than_an_official(self):
        official = entity_links(estate(), [org_parcel()], [entity()])[0]
        agent = entity_links(
            estate(), [org_parcel()], [entity(officials=[], registered_agent_name="John Q Public")]
        )[0]
        self.assertIn("registered_agent_link", agent["evidence"])
        self.assertLess(agent["score"], official["score"])
        self.assertIn("name_only_needs_human_review", agent["flags"])
        self.assertEqual(agent["via_entity"]["role_kind"], "registered_agent")

    def test_registered_agent_address_never_corroborates(self):
        ent = entity(
            officials=[],
            registered_agent_name="John Q Public",
            registered_agent_address="4210 Elm Street, Charlotte NC 28205",  # = estate PR address
        )
        link = entity_links(estate(), [org_parcel()], [ent])[0]
        self.assertNotIn("entity_office_address_match", link["evidence"])

    def test_same_name_official_at_an_unrelated_entity_does_not_link(self):
        other = entity(sos_id="3456789", entity_name="Other Ventures LLC")
        self.assertEqual(entity_links(estate(), [org_parcel()], [other]), [])

    def test_middle_initial_conflict_with_the_official_contradicts(self):
        ent = entity(officials=[{"person_name": "John V Public", "title": "Manager", "source": "AR"}])
        link = entity_links(estate(), [org_parcel()], [ent])[0]
        self.assertIn("middle_initial_conflict", link["evidence"])

    def test_entity_suffix_conflict_is_recorded(self):
        link = entity_links(estate(), [org_parcel(owner_name="NONESUCH HOLDINGS INC")], [entity()])[0]
        self.assertIn("entity_suffix_conflict", link["evidence"])

    def test_ambiguous_entity_name_is_evidence_not_a_guess(self):
        twin = entity(
            sos_id="7654321",
            entity_name="The Nonesuch Holdings, LLC",
            officials=[{"person_name": "Someone Else", "title": "Manager", "source": "AR"}],
        )
        links = entity_links(estate(), [org_parcel()], [entity(), twin])
        self.assertEqual(len(links), 1)
        self.assertIn("entity_name_ambiguous", links[0]["evidence"])

    def test_common_official_name_is_penalized(self):
        entities = [entity()] + [
            entity(sos_id=str(9000000 + i), entity_name="Unrelated {0} LLC".format(i)) for i in range(8)
        ]
        link = entity_links(estate(), [org_parcel()], entities)[0]
        self.assertIn("common_name", link["evidence"])

    def test_one_link_per_pair_when_decedent_is_official_and_agent(self):
        ent = entity(registered_agent_name="John Q Public")
        result = crossref.crossref([estate()], [org_parcel()], [], rules(), entities=[ent])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["via_entity"]["role_kind"], "official")
        self.assertEqual(result["run"]["collapsed_duplicates"], 1)

    def test_direct_owner_wins_over_entity_path_for_a_mixed_owner_string(self):
        mixed = org_parcel(owner_name="PUBLIC JOHN Q & NONESUCH HOLDINGS LLC")
        links = entity_links(estate(), [mixed], [entity()])
        self.assertEqual(len(links), 1)
        self.assertIsNone(links[0]["via_source"])

    def test_deed_from_decedent_into_the_entity_is_evidence(self):
        deed = {
            "county": "MECKLENBURG",
            "instrument_number": "2020-0000001",
            "recorded_date": "2020-01-10",
            "grantor_name": "PUBLIC JOHN Q",
            "grantee_name": "NONESUCH HOLDINGS LLC",
            "parcel_pin": "133-070-13",
        }
        link = entity_links(estate(), [org_parcel()], [entity()], deeds=[deed])[0]
        self.assertIn("deed_grantor_link", link["evidence"])
        self.assertEqual(link["flags"], ["held_via_entity"])

    def test_entity_that_is_not_active_is_flagged(self):
        link = entity_links(estate(), [org_parcel()], [entity(status="Admin. Dissolved")])[0]
        self.assertIn("entity_not_active", link["flags"])

    def test_running_without_entities_still_works(self):
        result = crossref.crossref([estate()], [parcel()], [], rules())
        self.assertEqual(len(result["matches"]), 1)
        self.assertIsNone(result["matches"][0]["via_source"])
        self.assertEqual(result["run"]["business_entities"], 0)


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = crossref.crossref(
            crossref.load_records(os.path.join(SAMPLE, "estate_cases.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "parcels.jsonl")),
            crossref.load_records(os.path.join(SAMPLE, "deeds.jsonl")),
            rules(),
            entities=crossref.load_records(os.path.join(SAMPLE, "business_entities.jsonl")),
        )

    def test_every_link_maps_onto_the_entity_match_table(self):
        columns = {
            "left_source", "left_id", "right_source", "right_id",
            "match_tier", "score", "evidence", "flags", "status", "via_source", "via_id",
        }
        for link in self.result["matches"]:
            self.assertTrue(columns.issubset(link.keys()))
            self.assertEqual(link["left_source"], "estate_case")
            self.assertEqual(link["right_source"], "parcel")
            self.assertTrue(0.0 <= link["score"] <= 1.0)
            self.assertIn(link["status"], ("confirmed", "pending", "rejected"))
            self.assertEqual(link["via_source"] is None, link["via_id"] is None)

    def test_every_emitted_label_has_a_weight(self):
        rule_set = rules()
        for link in self.result["matches"]:
            self.assertIn(link["evidence"][0], rule_set["name_base"])
            for label in link["evidence"][1:]:
                self.assertIn(label, rule_set["evidence"])

    def test_capped_links_are_pending_and_flagged_on_the_sample(self):
        by_pin = {link["right_id"]: link for link in self.result["matches"]}
        via = by_pin["MECKLENBURG/133-070-13"]
        self.assertEqual((via["status"], via["flags"], via["via_id"]), ("pending", ["held_via_entity"], "1234567"))
        self.assertEqual(via["score"], 1.0)
        agent = by_pin["MECKLENBURG/133-070-14"]
        self.assertEqual(agent["status"], "pending")
        self.assertEqual(agent["flags"], ["held_via_entity", "name_only_needs_human_review"])
        trust = by_pin["MECKLENBURG/017-455-03"]
        self.assertEqual((trust["status"], trust["flags"], trust["via_source"]), ("pending", ["held_in_trust"], None))
        self.assertNotIn("MECKLENBURG/133-070-16", by_pin)  # a family trust is unreachable
        self.assertEqual(self.result["run"]["collapsed_duplicates"], 0)

    def test_rollup_lists_capped_links_apart_without_a_soft_yes(self):
        rows = {row["left_id"]: row for row in self.result["estates"]}
        public = rows["MECKLENBURG/26 E 001234"]
        self.assertEqual([v["via_id"] for v in public["pending_via_entity"]], ["1234567"])
        self.assertNotIn("MECKLENBURG/133-070-13", public["pending_parcels"])
        testcase = rows["MECKLENBURG/26 E 001236"]
        self.assertEqual(testcase["pending_in_trust"], ["MECKLENBURG/017-455-03"])
        self.assertEqual(testcase["assessed_value_confirmed"], 389400.0)  # trust parcel not counted
        self.assertFalse(rows["MECKLENBURG/26 E 001240"]["has_real_property"])
        for row in rows.values():
            self.assertNotIn("may_hold_interest", " ".join(row.keys()))

    def test_expected_dispositions_on_the_sample(self):
        statuses = {}
        for link in self.result["matches"]:
            statuses.setdefault(link["status"], []).append(link["right_id"])
        self.assertEqual(sorted(statuses["confirmed"]), [
            "MECKLENBURG/017-455-02",
            "MECKLENBURG/045-121-08",
            "MECKLENBURG/213-002-44",
        ])

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
            entities=crossref.load_records(os.path.join(SAMPLE, "business_entities.jsonl")),
        )
        self.assertEqual(
            json.dumps(again, sort_keys=True), json.dumps(self.result, sort_keys=True)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
