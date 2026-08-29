"""Address normalization tests.

Every input string here was taken from live Mecklenburg CAMA data, so these
tests pin behavior against real formatting rather than invented examples.
"""

import sys
import unittest
from os.path import dirname, join

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.normalize.address import (  # noqa: E402
    normalize_address,
    same_address,
)


class TestNormalizeAddress(unittest.TestCase):
    def test_strips_city_and_state_tail_from_situs(self) -> None:
        self.assertEqual(normalize_address("12422 DOWNY BIRCH RD CHARLOTTE NC").key, "12422 DOWNY BIRCH RD")

    def test_multiword_city_is_stripped_as_a_unit(self) -> None:
        self.assertEqual(normalize_address("6235 HOLLOW OAK DR MINT HILL NC").key, "6235 HOLLOW OAK DR")

    def test_city_stripped_only_from_the_tail(self) -> None:
        # A street named after a town keeps its name; only the trailing
        # jurisdiction is removed.
        self.assertEqual(
            normalize_address("7301 MATTHEWS-MINT HILL RD MINT HILL NC").key,
            "7301 MATTHEWS MINT HILL RD",
        )

    def test_uninc_jurisdiction_is_stripped(self) -> None:
        # UNINC marks unincorporated county, not a street suffix. It appears on
        # roughly 5.5% of parcels and would otherwise poison the join key.
        self.assertEqual(normalize_address("4710 ALLISON ASHWORTH CT UNINC NC").key, "4710 ALLISON ASHWORTH CT")

    def test_missing_house_number_is_tolerated(self) -> None:
        parsed = normalize_address(" GOODMAN RD UNINC NC")
        self.assertEqual(parsed.key, "GOODMAN RD")
        self.assertIsNone(parsed.house_number)

    def test_hyphen_and_space_variants_collapse(self) -> None:
        # The same parcel carries situs '6307 CORY-BRET LN' and mailing
        # '6307 CORY BRET LN'; absentee detection depends on these matching.
        self.assertEqual(
            normalize_address("6307 CORY-BRET LN CHARLOTTE NC").key,
            normalize_address("6307 CORY BRET LN").key,
        )

    def test_unit_is_extracted_and_separable(self) -> None:
        parsed = normalize_address("210 N CHURCH ST UNIT 1104")
        self.assertEqual(parsed.key, "210 N CHURCH ST")
        self.assertEqual(parsed.unit, "1104")
        self.assertEqual(parsed.key_with_unit, "210 N CHURCH ST # 1104")

    def test_county_abbreviations_match_usps_long_forms(self) -> None:
        # Mecklenburg writes AV/CR/BV/WY/PY/TR where USPS writes
        # AVE/CIR/BLVD/WAY/PKWY/TRL. Both must reach the same key or no
        # cross-source address join works.
        pairs = [
            ("1901 PATTON AV CHARLOTTE NC", "1901 Patton Avenue"),
            ("7616 CARELOCK CR CHARLOTTE NC", "7616 Carelock Circle"),
            ("2505 SOUTH BV CHARLOTTE NC", "2505 South Boulevard"),
            ("9917 PALLISERS TR CHARLOTTE NC", "9917 Pallisers Trail"),
            ("419 LONG CREEK PY CHARLOTTE NC", "419 Long Creek Parkway"),
        ]
        for county_form, human_form in pairs:
            self.assertEqual(
                normalize_address(county_form).key,
                normalize_address(human_form).key,
                msg=county_form + " vs " + human_form,
            )

    def test_suffix_mapping_applies_to_final_token_only(self) -> None:
        # PARK stays PARK when it is the street name, not the suffix.
        self.assertEqual(normalize_address("100 PARK RD CHARLOTTE NC").key, "100 PARK RD")

    def test_empty_input_is_safe(self) -> None:
        for value in (None, "", "   "):
            parsed = normalize_address(value)
            self.assertTrue(parsed.is_empty)

    def test_same_address_requires_non_empty_key(self) -> None:
        blank = normalize_address("")
        self.assertFalse(same_address(blank, blank))
        real = normalize_address("100 PARK RD")
        self.assertTrue(same_address(real, normalize_address("100 Park Road")))


if __name__ == "__main__":
    unittest.main()
