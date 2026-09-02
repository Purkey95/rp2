#!/usr/bin/env python3
"""Tests for the map-driven intake engine.

Run directly (`python3 test_intake.py`) or under pytest from the repo root.
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402
from intake import adapt  # noqa: E402

FIXTURES = os.path.join(HERE, "sample", "intake")
MAPS = os.path.join(HERE, "intake", "maps")
ARGS = {"county": "MECKLENBURG", "base": "https://example.invalid/src", "retrieved_at": "2026-09-01T06:00:00+00:00", "since": None}


def run_map(map_name, csv_name, child=None, args=None):
    spec = adapt.load_map(os.path.join(MAPS, map_name + ".json"))
    rows = adapt.read_csv(os.path.join(FIXTURES, csv_name))
    child_rows = adapt.read_csv(os.path.join(FIXTURES, child)) if child else None
    return adapt.adapt(rows, spec, args or ARGS, child_rows)


class TestTyping(unittest.TestCase):
    def test_dates_normalize_from_the_formats_counties_use(self):
        for raw in ("2026-01-14", "01/14/2026", "1/14/2026", "20260114", "2026-01-14T00:00:00"):
            self.assertEqual(adapt.parse_date(raw), "2026-01-14", raw)
        self.assertIsNone(adapt.parse_date(""))
        with self.assertRaises(ValueError):
            adapt.parse_date("Jan 14th 2026")

    def test_numbers_drop_currency_noise_and_reject_words(self):
        self.assertEqual(adapt.parse_number("$312,500"), 312500)
        self.assertEqual(adapt.parse_number("402100.50"), 402100.5)
        with self.assertRaises(ValueError):
            adapt.parse_number("n/a")

    def test_template_over_empty_source_columns_is_absent_not_punctuation(self):
        field = {"template": "{A} {B}, {C}"}
        self.assertIsNone(adapt._value(field, {"A": "", "B": "", "C": ""}, {}))
        self.assertEqual(adapt._value(field, {"A": "1 Main", "B": "", "C": "NC"}, {}), "1 Main , NC")


class TestMaps(unittest.TestCase):
    def test_every_map_names_only_schema_columns(self):
        for name in os.listdir(MAPS):
            adapt.load_map(os.path.join(MAPS, name))  # raises MapError otherwise

    def test_a_map_naming_an_unknown_column_is_a_map_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"record_type": "parcel", "fields": {"owner_phone": {"from": "Phone"}}}, f)
        try:
            with self.assertRaises(adapt.MapError):
                adapt.load_map(f.name)
        finally:
            os.unlink(f.name)


class TestEstates(unittest.TestCase):
    def test_estate_file_numbers_only_and_nothing_inferred(self):
        records, rejects, filtered = run_map("estates", "estates_export.csv")
        self.assertEqual(filtered, 1)  # the CVD row
        self.assertEqual([r["file_number"] for r in records], ["26 E 001234", "26 E 001236", "26 E 001241"])
        self.assertEqual(rejects, [])
        johnson = records[2]
        self.assertNotIn("date_of_death", johnson)  # empty stays absent
        self.assertNotIn("personal_rep_name", johnson)
        self.assertEqual(records[0]["date_of_death"], "2026-01-14")
        self.assertEqual(records[0]["source_url"], "https://example.invalid/src/26 E 001234")
        self.assertEqual(records[0]["county"], "MECKLENBURG")

    def test_since_filters_on_filing_date(self):
        records, _, filtered = run_map("estates", "estates_export.csv", args=dict(ARGS, since="2026-03-01"))
        self.assertEqual([r["file_number"] for r in records], ["26 E 001241"])
        self.assertEqual(filtered, 3)

    def test_missing_required_column_is_a_reject_with_a_reason(self):
        spec = adapt.load_map(os.path.join(MAPS, "estates.json"))
        rows = [{"CaseNumber": "26 E 000001", "DecedentName": "", "FileDate": "01/01/2026"}]
        records, rejects, _ = adapt.adapt(rows, spec, ARGS)
        self.assertEqual(records, [])
        self.assertIn("required column decedent_name is missing", rejects[0]["reasons"])

    def test_duplicate_natural_key_is_a_reject_not_a_merge(self):
        spec = adapt.load_map(os.path.join(MAPS, "estates.json"))
        row = {"CaseNumber": "26 E 000001", "DecedentName": "A B", "FileDate": "01/01/2026"}
        records, rejects, _ = adapt.adapt([row, dict(row)], spec, ARGS)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(rejects), 1)
        self.assertIn("already seen at line 2", rejects[0]["reasons"][0])

    def test_unparseable_date_is_a_reject_not_a_blank(self):
        spec = adapt.load_map(os.path.join(MAPS, "estates.json"))
        rows = [{"CaseNumber": "26 E 000001", "DecedentName": "A B", "FileDate": "soon"}]
        _, rejects, _ = adapt.adapt(rows, spec, ARGS)
        self.assertIn("filing_date: unparseable date 'soon'", rejects[0]["reasons"])


class TestParcelsAndDeeds(unittest.TestCase):
    def test_owner_string_is_joined_the_way_the_assessor_prints_it(self):
        records, rejects, _ = run_map("meck_parcels", "meck_parcels.csv")
        self.assertEqual(rejects, [])
        by_pin = {r["pin"]: r for r in records}
        self.assertEqual(by_pin["045-121-08"]["owner_name"], "PUBLIC JOHN Q & JANE R")
        self.assertEqual(by_pin["133-070-13"]["owner_name"], "NONESUCH HOLDINGS LLC")
        self.assertEqual(by_pin["045-121-08"]["assessed_value"], 312500)
        self.assertEqual(by_pin["045-121-08"]["owner_mailing_address"], "4210 ELM ST APT 5 CHARLOTTE NC 28205")
        self.assertNotIn("deed_book", by_pin["133-070-13"])

    def test_deeds_keep_the_pin_as_printed(self):
        records, rejects, _ = run_map("meck_deeds", "meck_deeds.csv")
        self.assertEqual(rejects, [])
        self.assertEqual(records[0]["recorded_date"], "2026-04-02")
        self.assertEqual(records[0]["parcel_pin"], "017-455-02")
        self.assertEqual(records[0]["source_url"], "https://example.invalid/src/38221/0417")


class TestEntities(unittest.TestCase):
    def test_officials_are_nested_and_the_agent_stays_apart(self):
        records, rejects, _ = run_map("ncsos", "ncsos_corporations.csv", child="ncsos_officials.csv")
        self.assertEqual(rejects, [])
        by_id = {r["sos_id"]: r for r in records}
        nonesuch = by_id["1234567"]
        self.assertEqual([o["person_name"] for o in nonesuch["officials"]], ["John Q Public", "Jane R Public"])
        self.assertEqual(nonesuch["officials"][0]["source"], "ANNUAL REPORT 2025")
        self.assertEqual(nonesuch["registered_agent_name"], "Placeholder Registered Agents Inc")
        self.assertNotIn("county", nonesuch)
        self.assertTrue(nonesuch["domestic"])
        self.assertEqual(nonesuch["principal_office_address"], "4210 Elm Street Apt 5, Charlotte NC 28205")
        self.assertNotIn("mailing_address", by_id["3456789"])


class TestRoundTrip(unittest.TestCase):
    def test_adapter_output_runs_through_the_matcher_like_the_sample(self):
        estates, _, _ = run_map("estates", "estates_export.csv")
        parcels, _, _ = run_map("meck_parcels", "meck_parcels.csv")
        deeds, _, _ = run_map("meck_deeds", "meck_deeds.csv")
        entities, _, _ = run_map("ncsos", "ncsos_corporations.csv", child="ncsos_officials.csv")
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, records in (("estates", estates), ("parcels", parcels), ("deeds", deeds), ("entities", entities)):
                paths[name] = os.path.join(tmp, name + ".jsonl")
                adapt.write_jsonl(paths[name], records)
            loaded = {name: crossref.load_records(path) for name, path in paths.items()}
        result = crossref.crossref(loaded["estates"], loaded["parcels"], loaded["deeds"], crossref.load_rules(), entities=loaded["entities"])
        by_pin = {l["right_id"]: l for l in result["matches"]}
        self.assertEqual(by_pin["MECKLENBURG/045-121-08"]["status"], "confirmed")
        self.assertEqual(by_pin["MECKLENBURG/017-455-02"]["status"], "confirmed")
        self.assertEqual(by_pin["MECKLENBURG/133-070-13"]["flags"], ["held_via_entity"])
        self.assertEqual(by_pin["MECKLENBURG/017-455-03"]["flags"], ["held_in_trust"])
        self.assertEqual([r["left_id"] for r in result["skipped_estates"]], ["MECKLENBURG/26 E 001241"])


class TestCli(unittest.TestCase):
    def test_rejects_make_the_exit_status_nonzero_but_good_rows_are_still_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.csv")
            with open(src, "w", encoding="utf-8") as f:
                f.write("CaseNumber,DecedentName,FileDate\n26 E 000001,A B,01/01/2026\n26 E 000002,,01/01/2026\n")
            out = os.path.join(tmp, "out.jsonl")
            status = adapt.main(["--input", src, "--map", os.path.join(MAPS, "estates.json"), "--out", out, "--county", "MECKLENBURG"])
            self.assertEqual(status, 1)
            self.assertEqual(len(crossref.load_records(out)), 1)
            self.assertEqual(len(crossref.load_records(out + ".rejects.jsonl")), 1)
            status = adapt.main(["--input", src, "--map", os.path.join(MAPS, "estates.json"), "--out", out, "--county", "MECKLENBURG", "--allow-rejects"])
            self.assertEqual(status, 0)

    def test_county_is_required_for_county_sources(self):
        with self.assertRaises(SystemExit):
            adapt.main(["--input", os.path.join(FIXTURES, "estates_export.csv"), "--map", os.path.join(MAPS, "estates.json"), "--out", os.devnull])


if __name__ == "__main__":
    unittest.main(verbosity=2)
