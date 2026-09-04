import json
import unittest

from mclt_helpers import COUNTY, FIXTURES, fixture_bytes, fresh_store, ingested

from monitorclt import history, ingest, quality
from monitorclt.counties import mecklenburg
from monitorclt.sources.contract import check, validate
from monitorclt.sources.html_tables import find_table, parse_tables
from monitorclt.sources.transport import FixtureTransport


class IngestTests(unittest.TestCase):
    def test_fixture_ingest_counts_and_events(self):
        s = ingested()
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'estate_case'"), 8)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM parcel"), 16)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'estate_opened'"), 8)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'deed_recorded'"), 3)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM mention WHERE role = 'decedent' AND current = 1"), 8)
        # a Union parcel in the Mecklenburg feed keeps its own county
        self.assertEqual(s.scalar("SELECT county FROM parcel WHERE pin = '07-190-225'"), "UNION")

    def test_idempotent(self):
        s = ingested()
        for r in ingest.ingest_county(s, COUNTY, FixtureTransport(FIXTURES), profile="sample"):
            self.assertEqual((r["rows_new"], r["rows_changed"], r["rows_retired"]), (0, 0, 0), r["connector"])
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM raw_capture"), 7)

    def test_snapshot_change_and_retire(self):
        s = ingested()
        parcels = [json.loads(line) for line in fixture_bytes("parcels.jsonl").decode().splitlines() if line.strip()]
        parcels[0]["owner_name"] = "NEWOWNER SALLY"
        parcels[0]["assessed_value"] = 400000
        dropped = parcels.pop()  # last parcel disappears from the roll
        body = "\n".join(json.dumps(p) for p in parcels).encode()
        s.clock.advance(days=1)
        r = ingest.ingest(s, mecklenburg.ParcelConnector(), FixtureTransport(bodies={"parcels.jsonl": body}))
        self.assertEqual((r["rows_changed"], r["rows_retired"]), (1, 1))
        kinds = sorted(e["kind"] for e in r["events"])
        self.assertEqual(kinds, ["assessed_value_changed", "owner_changed"])
        self.assertIsNotNone(s.scalar("SELECT retired_at FROM parcel WHERE pin = ?", (dropped["pin"],)))
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'parcel_retired'"), 1)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM mention WHERE source = 'parcel' AND natural_key = '045-121-08' AND current = 1"), 1)
        self.assertEqual(s.scalar("SELECT raw_name FROM mention WHERE source = 'parcel' AND natural_key = '045-121-08' AND current = 1"), "NEWOWNER SALLY")

    def test_incremental_status_transition(self):
        s = ingested()
        with open(FIXTURES + "/golden/estate_case.json", encoding="utf-8") as f:
            estates = json.load(f)
        estates[0]["case_status"] = "CLOSED"
        body = json.dumps(estates).encode()
        r = ingest.ingest(s, mecklenburg.EstateCaseConnector(endpoint="fixture://estates.json"), FixtureTransport(bodies={"estates.json": body}))
        self.assertEqual(r["rows_changed"], 1)
        self.assertEqual(sorted(e["kind"] for e in r["events"]), ["estate_closed", "estate_status_changed"])

    def test_bad_rows_are_counted_not_fatal(self):
        s = fresh_store()
        body = b'{"pin": "1", "owner_name": "A B"}\n{"owner_name": "missing pin"}\n'
        r = ingest.ingest(s, mecklenburg.ParcelConnector(), FixtureTransport(bodies={"parcels.jsonl": body}))
        self.assertEqual((r["status"], r["rows_new"], r["rows_failed"]), ("ok", 1, 1))
        self.assertIn("missing required", r["error"])

    def test_transport_failure_fails_run_and_keeps_watermark(self):
        s = ingested()
        before = s.scalar("SELECT value FROM watermark WHERE connector = 'parcel'")
        r = ingest.ingest(s, mecklenburg.ParcelConnector(), FixtureTransport(bodies={}))
        self.assertEqual(r["status"], "failed")
        self.assertEqual(s.scalar("SELECT value FROM watermark WHERE connector = 'parcel'"), before)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'parcel' AND retired = 1"), 0)


class HtmlAndContractTests(unittest.TestCase):
    def test_html_tables(self):
        tables = parse_tables(fixture_bytes("estate_cases.html"))
        self.assertEqual(len(tables), 2)
        rows = find_table(fixture_bytes("estate_cases.html"), ["File Number", "Decedent"])
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["Decedent"], "John Q Public")

    def test_html_without_th_uses_first_row(self):
        rows = parse_tables("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2<br>3</td></tr></table>")[0]
        self.assertEqual(rows, [{"a": "1", "b": "2 3"}])

    def test_contract_golden(self):
        for c in mecklenburg.connectors():
            r = check(c, FIXTURES, FIXTURES + "/golden/" + c.name + ".json")
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["golden"], "ok")

    def test_validate_flags_duplicates(self):
        c = mecklenburg.ParcelConnector()
        problems = validate(c, [{"pin": "1", "owner_name": "A"}, {"pin": "1", "owner_name": "B"}, {"owner_name": "C"}])
        self.assertEqual(len(problems), 3)  # duplicate key, missing required, empty key


class QualityTests(unittest.TestCase):
    def test_zero_rows_and_drift_are_anomalies(self):
        s = ingested()
        s.clock.advance(hours=48)
        r = ingest.ingest(
            s,
            mecklenburg.DeedConnector(),
            FixtureTransport(
                bodies={
                    "deeds.jsonl": b'{"instrument_number":"X","recorded_date":"2026-01-01","grantor_name":"A B","grantee_name":"C D","parcel_pin":"1","new_field":1}'
                }
            ),
        )
        self.assertEqual(r["rows_new"], 1)
        ingest.ingest(s, mecklenburg.ParcelConnector(), FixtureTransport(bodies={"parcels.jsonl": b""}))
        rows = {r["connector"]: r for r in quality.snapshot(s)}
        self.assertTrue(any(a.startswith("schema_drift") for a in rows["deed"]["anomalies"]), rows["deed"])
        self.assertIn("zero_rows", rows["parcel"]["anomalies"])
        self.assertTrue(any(a.startswith("stale") for a in rows["estate_case"]["anomalies"]))
        text = quality.format_status(quality.latest(s))
        self.assertIn("zero_rows", text)
        # only the counties the feed covered are retired; the Union row stays
        self.assertEqual(len(history.current_records(s, "parcel", "MECKLENBURG")), 0)


if __name__ == "__main__":
    unittest.main()
