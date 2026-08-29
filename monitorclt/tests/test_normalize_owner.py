"""Owner classification tests.

The false-positive cases are the point of this file. Against live Mecklenburg
data a substring matcher for estate signals is wrong far more often than it is
right, so each trap below is pinned explicitly.
"""

import sys
import unittest
from os.path import dirname, join

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.normalize.owner import (  # noqa: E402
    OwnerType,
    parse_owner,
    split_care_of,
)


def classify(full, last=None, first=None, sec_last=None, sec_first=None):
    return parse_owner(
        full, surname=last, given=first, secondary_surname=sec_last, secondary_given=sec_first
    )


class TestOwnerClassification(unittest.TestCase):
    def test_individual(self) -> None:
        owner = classify("MONDESIRE THERESA", "MONDESIRE", "THERESA")
        self.assertEqual(owner.owner_type, OwnerType.PERSON)
        self.assertEqual(str(owner.persons[0]), "THERESA MONDESIRE")

    def test_two_people_become_a_couple(self) -> None:
        owner = classify("FOSTER NATALIE", "FOSTER", "NATALIE", "FOSTER", "RANDY")
        self.assertEqual(owner.owner_type, OwnerType.COUPLE)
        self.assertEqual(len(owner.persons), 2)

    def test_company(self) -> None:
        self.assertEqual(classify("PROGRESS RESDENTIAL  BORROWER 1  LLC").owner_type, OwnerType.COMPANY)

    def test_trust(self) -> None:
        self.assertEqual(classify("THE GELPI LIVING  TRUST").owner_type, OwnerType.TRUST)

    def test_estate_and_heirs_are_decedent_markers(self) -> None:
        for value in ("JOSEPH P BAGWELL ESTATE", "CONRAD WALTER H HEIRS", "BLACK CLINT M (HEIRS)"):
            self.assertTrue(classify(value).indicates_decedent, msg=value)

    def test_life_estate(self) -> None:
        owner = classify("CLAWSON KATIE W (LIFE ESTATE)")
        self.assertEqual(owner.owner_type, OwnerType.LIFE_ESTATE)
        self.assertTrue(owner.indicates_decedent)


class TestSubstringTraps(unittest.TestCase):
    """Cases where naive substring matching gives the wrong answer.

    A ``LIKE '%ESTATE%'`` filter returns 1,119 "REAL ESTATE" companies against
    roughly 57 genuine estate owners -- a ~95% false-positive rate.
    """

    def test_real_estate_company_is_not_an_estate(self) -> None:
        for value in (
            "HUNTERSVILLE CROSSING REAL ESTATE LLC",
            "PINNACLE REAL ESTATE HOLDINGS LLC",
            "S&R REAL ESTATE INVESTMENT LLC",
        ):
            owner = classify(value)
            self.assertEqual(owner.owner_type, OwnerType.COMPANY, msg=value)
            self.assertFalse(owner.indicates_decedent, msg=value)

    def test_etal_substring_does_not_match_names_containing_it(self) -> None:
        for value, last, first in (
            ("VETAL DONALD III", "VETAL", "DONALD III"),
            ("RISHI SHEETAL", "RISHI", "SHEETAL"),
            ("PATEL HETALBEN  A", "PATEL", "HETALBEN  A"),
        ):
            self.assertEqual(classify(value, last, first).owner_type, OwnerType.PERSON, msg=value)

    def test_metals_freedom_is_a_company_not_an_etal_record(self) -> None:
        self.assertEqual(classify("METALS FREEDOM INC ").owner_type, OwnerType.COMPANY)

    def test_life_substring_does_not_imply_life_estate(self) -> None:
        self.assertEqual(classify("ROADSTER LIFE LLC").owner_type, OwnerType.COMPANY)
        self.assertEqual(classify("GRACELIFE CHURCH INC ").owner_type, OwnerType.ORGANIZATION)
        self.assertEqual(classify("LIFELD MICHAEL  A", "LIFELD", "MICHAEL  A").owner_type, OwnerType.PERSON)

    def test_heirs_church_is_an_organization(self) -> None:
        owner = classify("HEIRS CHRISTIAN CENTER CHURCH")
        self.assertEqual(owner.owner_type, OwnerType.ORGANIZATION)
        self.assertFalse(owner.indicates_decedent)

    def test_company_marker_beats_heirs_marker(self) -> None:
        # 'HALL JOHNSTON HEIRS LLC' is an operating company, not an estate.
        owner = classify("HALL JOHNSTON HEIRS LLC ")
        self.assertEqual(owner.owner_type, OwnerType.COMPANY)
        self.assertFalse(owner.indicates_decedent)


class TestCountySplitIsNotTrusted(unittest.TestCase):
    """The county's first/last split mangles non-person owners."""

    def test_trust_does_not_yield_a_person_named_trust(self) -> None:
        # County columns are last='THE GELPI LIVING ', first='TRUST'.
        owner = classify("THE GELPI LIVING  TRUST", "THE GELPI LIVING ", "TRUST")
        self.assertEqual(owner.persons, ())

    def test_estate_does_not_yield_a_person_named_estate(self) -> None:
        # The decedent's name IS recovered (see TestDecedentNameRecovery), but
        # the marker word the county left in the first-name column must never
        # survive into it.
        owner = classify("JOSEPH P BAGWELL ESTATE", "JOSEPH P BAGWELL", "ESTATE")
        self.assertEqual(len(owner.persons), 1)
        self.assertNotIn("ESTATE", str(owner.persons[0]))
        self.assertEqual(owner.persons[0].given, "JOSEPH P")


class TestCareOf(unittest.TestCase):
    def test_care_of_prefix_is_not_a_co_owner(self) -> None:
        co_owner, care_of = split_care_of("C/O DALE CONRAD")
        self.assertIsNone(co_owner)
        self.assertEqual(care_of, "DALE CONRAD")

    def test_attn_prefix_is_recognized(self) -> None:
        self.assertEqual(split_care_of("ATTN RYAN BAGWELL")[1], "RYAN BAGWELL")

    def test_plain_secondary_owner_is_a_co_owner(self) -> None:
        co_owner, care_of = split_care_of("FOSTER RANDY")
        self.assertEqual(co_owner, "FOSTER RANDY")
        self.assertIsNone(care_of)

    def test_corporate_agent_line_is_care_of(self) -> None:
        owner = classify("FKH SFR C2 LP", "FKH SFR C2 LP", None, "C/O FIRSTKEY HOMES  LLC", None)
        self.assertEqual(owner.care_of, "FIRSTKEY HOMES LLC")
        self.assertEqual(owner.persons, ())

    def test_blank_variants_are_absent(self) -> None:
        for value in (None, "", "   "):
            self.assertEqual(split_care_of(value), (None, None))


class TestDecedentNameRecovery(unittest.TestCase):
    """Decedent records must be searchable by the decedent's name.

    These are the records an obituary most needs to match, so omitting them
    from the name index would omit the case the index exists for. The two
    layouts differ in name order and both must resolve correctly.
    """

    def test_county_order_heirs_record(self) -> None:
        person = classify("CONRAD WALTER H HEIRS", "CONRAD", "WALTER H HEIRS").persons[0]
        self.assertEqual(person.surname, "CONRAD")
        self.assertEqual(person.given, "WALTER H")

    def test_natural_order_estate_record(self) -> None:
        # Marker occupies the whole first column, so the name is natural order.
        person = classify("JOSEPH P BAGWELL ESTATE", "JOSEPH P BAGWELL", "ESTATE").persons[0]
        self.assertEqual(person.surname, "BAGWELL")
        self.assertEqual(person.given, "JOSEPH P")

    def test_parenthesized_marker_is_stripped(self) -> None:
        person = classify("BLACK CLINT M (HEIRS)", "BLACK", "CLINT M (HEIRS)").persons[0]
        self.assertEqual(person.surname, "BLACK")
        self.assertEqual(person.given, "CLINT M")

    def test_marker_inside_surname_column(self) -> None:
        person = classify("PERRY (HEIRS) NICK", "PERRY (HEIRS)", "NICK").persons[0]
        self.assertEqual(person.surname, "PERRY")
        self.assertEqual(person.given, "NICK")

    def test_life_estate_name_is_recovered(self) -> None:
        person = classify("CLAWSON KATIE W (LIFE ESTATE)", "CLAWSON", "KATIE W (LIFE ESTATE)").persons[0]
        self.assertEqual(person.surname, "CLAWSON")
        self.assertEqual(person.given, "KATIE W")

    def test_company_with_heirs_in_the_name_yields_no_person(self) -> None:
        self.assertEqual(classify("HALL JOHNSTON HEIRS LLC ", "HALL JOHNSTON HEIRS LLC", "").persons, ())

    def test_no_marker_word_survives_into_a_name(self) -> None:
        for full, last, first in (
            ("CONRAD WALTER H HEIRS", "CONRAD", "WALTER H HEIRS"),
            ("SANDRA H NEWTON ESTATE", "SANDRA H NEWTON", "ESTATE"),
            ("CLAWSON KATIE W (LIFE ESTATE)", "CLAWSON", "KATIE W (LIFE ESTATE)"),
        ):
            person = classify(full, last, first).persons[0]
            rendered = str(person)
            for marker in ("HEIRS", "ESTATE", "LIFE"):
                self.assertNotIn(marker, rendered, msg=full)


class TestNameSuffixes(unittest.TestCase):
    def test_suffix_is_separated_from_given_name(self) -> None:
        person = classify("SMITH THOMAS L JR", "SMITH", "THOMAS L JR").persons[0]
        self.assertEqual(person.given, "THOMAS L")
        self.assertEqual(person.suffix, "JR")

    def test_blocking_keys(self) -> None:
        person = classify("SMITH THOMAS L JR", "SMITH", "THOMAS L JR").persons[0]
        self.assertEqual(person.blocking_key, "SMITH|THOMAS")
        self.assertEqual(person.loose_blocking_key, "SMITH|T")


if __name__ == "__main__":
    unittest.main()
