"""End-to-end tests over a fixture captured from the live county service.

``tests/fixtures/mecklenburg_cama_sample.json`` holds 35 real records chosen to
cover the awkward cases: condo buildings that share a ``pid``, decedent-marked
owners, out-of-state mailing addresses, ``REAL ESTATE`` companies, ``UNINC``
situs addresses and county-style street suffixes. No network access is needed.
"""

import json
import sys
import unittest
from collections import Counter
from datetime import datetime, timezone
from os.path import dirname, join

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.normalize.owner import OwnerType  # noqa: E402
from monitorclt.parcel.enrich import MatchTier, ParcelIndex, parse_person_query  # noqa: E402
from monitorclt.parcel.model import Parcel  # noqa: E402
from monitorclt.parcel.store import ParcelStore  # noqa: E402

FIXTURE = join(dirname(__file__), "fixtures", "mecklenburg_cama_sample.json")
OBSERVED_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)


def load_fixture_parcels():
    with open(FIXTURE) as handle:
        payload = json.load(handle)
    return [
        Parcel.from_arcgis(feature["attributes"], observed_at=OBSERVED_AT, source="fixture")
        for feature in payload["features"]
    ]


class TestParcelModel(unittest.TestCase):
    def setUp(self) -> None:
        self.parcels = load_fixture_parcels()

    def test_fixture_contains_pid_collisions(self) -> None:
        # Guards the reason parcel_key is camapid: pid is not unique. Countywide
        # 428,504 records carry only 396,310 distinct pids.
        pid_counts = Counter(p.pid for p in self.parcels if p.pid)
        self.assertTrue(any(count > 1 for count in pid_counts.values()))

    def test_parcel_key_is_unique_where_pid_is_not(self) -> None:
        keys = [p.parcel_key for p in self.parcels]
        self.assertEqual(len(keys), len(set(keys)))

    def test_epoch_millis_sale_date_is_parsed(self) -> None:
        dated = [p for p in self.parcels if p.last_sale_date is not None]
        self.assertTrue(dated)
        for parcel in dated:
            self.assertGreater(parcel.last_sale_date.year, 1900)
            self.assertLessEqual(parcel.last_sale_date.year, 2026)

    def test_ownership_years_is_derived_from_observation_date(self) -> None:
        dated = [p for p in self.parcels if p.last_sale_date is not None]
        parcel = dated[0]
        expected = round((OBSERVED_AT.date() - parcel.last_sale_date).days / 365.25, 1)
        self.assertEqual(parcel.ownership_years, expected)

    def test_out_of_state_owners_are_flagged(self) -> None:
        out_of_state = [p for p in self.parcels if p.is_out_of_state]
        self.assertTrue(out_of_state)
        for parcel in out_of_state:
            self.assertNotEqual(parcel.mailing.state, "NC")
            self.assertTrue(parcel.is_absentee)
            self.assertTrue(parcel.is_out_of_area)

    def test_dirty_state_values_normalize_before_comparison(self) -> None:
        """Case and whitespace in the state column must not read as out-of-state.

        The county stores 'NC ', 'nc' and 'NC   ' alongside 'NC'. A literal
        ``txt_state <> 'NC'`` filter counts 105 in-state parcels as
        out-of-state, and a blank ' ' is missed by ``txt_state = ''`` too.
        Normalizing first is what makes the two populations reconcile.
        """
        for raw_state, expect_out_of_state, expect_international in (
            ("NC", False, False),
            ("NC ", False, False),
            ("nc", False, False),
            ("NC   ", False, False),
            (" ", False, True),
            ("", False, True),
            (None, False, True),
            ("SC", True, False),
            ("tx", True, False),
        ):
            parcel = Parcel.from_arcgis(
                {
                    "camapid": "TEST",
                    "full_owner_name": "DOE JANE",
                    "nme_ownerlastname": "DOE",
                    "nme_ownerfirstname": "JANE",
                    "situsaddress1": "100 MAIN ST CHARLOTTE NC",
                    "txt_mailaddr1": "500 ELSEWHERE RD",
                    "txt_city": "SOMEWHERE",
                    "txt_state": raw_state,
                },
                observed_at=OBSERVED_AT,
                source="synthetic",
            )
            self.assertEqual(parcel.is_out_of_state, expect_out_of_state, msg=repr(raw_state))
            self.assertEqual(parcel.is_international, expect_international, msg=repr(raw_state))

    def test_international_owners_are_out_of_area_not_out_of_state(self) -> None:
        # 174 parcels countywide mail abroad with empty city/state columns
        # ('1400-3280 BLOOR ST W', 'PARC DU CHATEAU'). Treating an absent state
        # as local would count them as owner-occupied.
        international = [p for p in self.parcels if p.is_international]
        self.assertTrue(international, "fixture should contain foreign mailing addresses")
        for parcel in international:
            self.assertIsNone(parcel.mailing.state)
            self.assertFalse(parcel.is_out_of_state)
            self.assertTrue(parcel.is_out_of_area)
            self.assertTrue(parcel.is_absentee)
            self.assertIn("outside the US", " ".join(parcel.evidence_summary()))

    def test_owner_occupied_parcels_are_not_absentee(self) -> None:
        # Situs carries a city/state tail and mailing does not; absentee
        # detection is meaningless unless both normalize to the same key.
        occupied = [
            p for p in self.parcels
            if p.situs.key and p.mailing.key and p.situs.key == p.mailing.key
        ]
        self.assertTrue(occupied)
        for parcel in occupied:
            self.assertFalse(parcel.is_absentee)

    def test_evidence_summary_is_facts_not_a_score(self) -> None:
        for parcel in self.parcels:
            for fact in parcel.evidence_summary():
                self.assertIsInstance(fact, str)
        decedents = [p for p in self.parcels if p.indicates_decedent]
        self.assertTrue(decedents)
        self.assertTrue(any("marked" in f for f in decedents[0].evidence_summary()))


class TestParcelStoreAndIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ParcelStore(":memory:")
        run_id = self.store.start_run("fixture", "1=1")
        self.written = self.store.upsert(load_fixture_parcels(), run_id=run_id)
        self.store.finish_run(run_id, self.written)
        self.index = ParcelIndex(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_all_fixture_records_are_stored(self) -> None:
        self.assertEqual(self.written, len(load_fixture_parcels()))
        self.assertEqual(self.store.stats()["parcels"], self.written)

    def test_load_run_is_recorded_for_provenance(self) -> None:
        runs = self.store.query("SELECT * FROM load_runs")
        self.assertEqual(len(runs), 1)
        self.assertIsNotNone(runs[0]["finished_at"])
        self.assertEqual(runs[0]["record_count"], self.written)

    def test_observed_at_is_stored_on_every_row(self) -> None:
        # Required for the backtest: replaying a signal needs to know when each
        # fact was observed, not just what it is.
        rows = self.store.query("SELECT observed_at FROM parcels")
        self.assertTrue(all(row["observed_at"] == OBSERVED_AT.isoformat() for row in rows))

    def test_upsert_is_idempotent(self) -> None:
        before = self.store.stats()["parcels"]
        self.store.upsert(load_fixture_parcels())
        self.assertEqual(self.store.stats()["parcels"], before)

    def test_address_lookup_returns_all_units_at_a_shared_address(self) -> None:
        rows = self.store.query(
            "SELECT situs_raw, COUNT(*) AS n FROM parcels WHERE situs_key IS NOT NULL "
            "GROUP BY situs_key HAVING n > 1 LIMIT 1"
        )
        self.assertTrue(rows, "fixture should contain a shared condo address")
        found = self.index.by_address(rows[0]["situs_raw"])
        self.assertGreater(len(found), 1)

    def test_address_lookup_is_empty_for_unknown_address(self) -> None:
        self.assertEqual(self.index.by_address("999999 NOWHERE RD CHARLOTTE NC"), [])
        self.assertEqual(self.index.by_address(""), [])

    def test_decedent_marked_excludes_real_estate_companies(self) -> None:
        for row in self.index.decedent_marked():
            self.assertNotEqual(row["owner_type"], OwnerType.COMPANY)
            self.assertIn(row["owner_type"], (OwnerType.ESTATE, OwnerType.HEIRS, OwnerType.LIFE_ESTATE))

    def test_international_flag_is_persisted(self) -> None:
        rows = self.store.query("SELECT * FROM parcels WHERE is_international = 1")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["is_out_of_state"], 0)
            self.assertEqual(row["is_out_of_area"], 1)
            self.assertIsNone(row["mail_state"])

    def test_real_estate_companies_are_present_but_not_decedents(self) -> None:
        rows = self.store.query("SELECT * FROM parcels WHERE owner_raw LIKE '%REAL ESTATE%'")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["indicates_decedent"], 0)


class TestPersonCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ParcelStore(":memory:")
        self.store.upsert(load_fixture_parcels())
        self.index = ParcelIndex(self.store)
        row = self.store.query(
            "SELECT o.surname, o.given, p.situs_city FROM owner_names o "
            "JOIN parcels p ON p.parcel_key = o.parcel_key "
            "WHERE o.given IS NOT NULL AND o.given <> '' LIMIT 1"
        )[0]
        self.surname = row["surname"]
        self.given = row["given"]
        self.city = row["situs_city"]

    def tearDown(self) -> None:
        self.store.close()

    def test_query_name_is_parsed_in_natural_order(self) -> None:
        person = parse_person_query("John A. Smith Jr")
        self.assertEqual(person.surname, "SMITH")
        self.assertEqual(person.given, "JOHN A")
        self.assertEqual(person.suffix, "JR")

    def test_known_owner_is_found(self) -> None:
        candidates = self.index.candidates_for_person(self.given + " " + self.surname)
        self.assertTrue(candidates)
        self.assertTrue(all(self.surname in c.matched_name for c in candidates))

    def test_city_match_promotes_to_strong(self) -> None:
        # The two-signal rule: an exact name alone is only MODERATE.
        without_city = self.index.candidates_for_person(self.given + " " + self.surname)
        self.assertEqual(without_city[0].tier, MatchTier.MODERATE)
        if self.city:
            with_city = self.index.candidates_for_person(
                self.given + " " + self.surname, city=self.city
            )
            self.assertEqual(with_city[0].tier, MatchTier.STRONG)

    def test_same_initial_different_name_is_weak_and_suppressed_by_default(self) -> None:
        # Blocking is on surname + first initial, so a different given name with
        # the same initial still lands in the candidate pool -- and must be
        # classified WEAK and hidden unless explicitly requested.
        near_miss = self.given.split(" ")[0][:1] + "ZZQX " + self.surname
        self.assertEqual(self.index.candidates_for_person(near_miss), [])
        weak = self.index.candidates_for_person(near_miss, include_weak=True)
        self.assertTrue(weak)
        self.assertTrue(all(c.tier == MatchTier.WEAK for c in weak))
        self.assertTrue(all("only" in " ".join(c.evidence) for c in weak))

    def test_different_initial_does_not_block_in(self) -> None:
        self.assertEqual(
            self.index.candidates_for_person("Qzxwvu " + self.surname, include_weak=True), []
        )

    def test_decedent_marked_parcels_are_searchable_by_person(self) -> None:
        # The obituary-to-parcel case: an ESTATE/HEIRS owner must be findable
        # by the decedent's name, and the decedent marker itself counts as a
        # corroborating signal.
        rows = self.store.query(
            "SELECT o.surname, o.given FROM owner_names o JOIN parcels p "
            "ON p.parcel_key = o.parcel_key WHERE p.indicates_decedent = 1 LIMIT 1"
        )
        self.assertTrue(rows, "decedent parcels must appear in the name index")
        candidates = self.index.candidates_for_person(rows[0]["given"] + " " + rows[0]["surname"])
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].tier, MatchTier.STRONG)
        self.assertTrue(any("marked" in e for e in candidates[0].evidence))

    def test_unknown_surname_returns_nothing(self) -> None:
        self.assertEqual(self.index.candidates_for_person("Nobody Qzxwvu"), [])

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(self.index.candidates_for_person(""), [])

    def test_every_candidate_requires_review(self) -> None:
        # There is deliberately no is_match flag and no confidence number.
        for candidate in self.index.candidates_for_person(self.given + " " + self.surname):
            self.assertTrue(candidate.requires_review)
            self.assertTrue(candidate.evidence)
            self.assertFalse(hasattr(candidate, "confidence"))


if __name__ == "__main__":
    unittest.main()
