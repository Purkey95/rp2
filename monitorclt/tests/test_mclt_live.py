"""Tests against the captured City of Charlotte ArcGIS pages (fixtures/mecklenburg/live):
the ArcGIS connector base, the assessor owner-string composition, the live profile end
to end, geocoding from address points, and the new parcel signals."""

import json
import os
import unittest

from mclt_helpers import COUNTY, fresh_store

from monitorclt import entities, geocode, ingest, pipeline, signals
from monitorclt.counties import mecklenburg_live as L
from monitorclt.normalize.names import parse_name
from monitorclt.sources import arcgis
from monitorclt.sources.base import registry
from monitorclt.sources.contract import check
from monitorclt.sources.transport import FixtureTransport

LIVE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "mecklenburg", "live"))


def live_store():
    s = fresh_store("2026-09-04T00:00:00Z")
    results = ingest.ingest_county(s, COUNTY, FixtureTransport(LIVE), profile="live", endpoints=L.fixture_endpoints())
    assert all(r.ok for r in results), [r["error"] for r in results if not r.ok]
    entities.backfill_parcel_coordinates(s)
    return s


class OwnerCompositionTests(unittest.TestCase):
    def test_compose_owner_real_forms(self):
        cases = {
            ("PONDS", "CLARLISSA MAE"): "PONDS CLARLISSA MAE",
            ("ESTATE OF ", "GRACIELA G WALL"): "ESTATE OF GRACIELA G WALL",
            ("KEITH ROBIN DUTOIT", "ESTATE"): "ESTATE OF KEITH ROBIN DUTOIT",
            ("JOHNNIE M DOUGLAS ", "ESTATE OF"): "ESTATE OF JOHNNIE M DOUGLAS",
            ("VENTURI", "THE ESTATE OF KATHRYN"): "ESTATE OF KATHRYN VENTURI",
            ("KAREN L JOHNSON LIVING", "TRUST"): "KAREN L JOHNSON LIVING TRUST",
            ("SHON REALESTATE LLC", None): "SHON REALESTATE LLC",
            (None, None): None,
        }
        for (last, first), want in cases.items():
            self.assertEqual(L.compose_owner(last, first), want, (last, first))

    def test_composed_strings_parse_as_the_right_person(self):
        n = parse_name(L.compose_owner("KEITH ROBIN DUTOIT", "ESTATE"), "last_first")
        self.assertEqual((n.first, n.middle, n.last, n.markers), ("KEITH", "ROBIN", "DUTOIT", ["ESTATE OF"]))
        n = parse_name(L.compose_owner("VENTURI", "THE ESTATE OF KATHRYN"), "last_first")
        self.assertEqual((n.first, n.last), ("KATHRYN", "VENTURI"))
        n = parse_name(L.compose_owner("KAREN L JOHNSON LIVING", "TRUST"), "last_first")
        self.assertEqual((n.first, n.middle, n.last, n.trust), ("KAREN", "L", "JOHNSON", True))
        n = parse_name(L.compose_owner("BLACK", "CLINT M (HEIRS)"), "last_first")
        self.assertEqual((n.first, n.last, n.markers), ("CLINT", "BLACK", ["HEIRS"]))
        n = parse_name(L.compose_owner("THE TOWNES FAMILY", "TRUST"), "last_first")
        self.assertTrue(n.is_organization and n.trust)

    def test_routing_parties(self):
        self.assertEqual(L.split_routing("PAUL K THAMES", "C/O"), (None, "PAUL K THAMES"))
        self.assertEqual(L.split_routing("C/O KEVIN DOUGLAS", None), (None, "KEVIN DOUGLAS"))
        self.assertEqual(L.split_routing("ATTN RYAN BAGWELL", None), (None, "RYAN BAGWELL"))
        self.assertEqual(L.split_routing("HYMAN", "ISIAH DARNELL"), ("HYMAN ISIAH DARNELL", None))
        self.assertEqual(L.split_routing("", ""), (None, None))

    def test_situs_and_mailing(self):
        a = {
            "houseno": "3508",
            "stdir": "",
            "stname": "WEDDINGTON",
            "sttype": "RD",
            "stsuffix": "",
            "houseunit": None,
            "municipality": "CHARLOTTE",
            "mailaddr1": "3508 WEDDINGTON RD",
            "mailaddr2": None,
            "city": "MATTHEWS",
            "state": "NC",
            "zipcode": "28105",
        }
        self.assertEqual(L.situs(a), "3508 WEDDINGTON RD, CHARLOTTE NC")
        self.assertEqual(L.mailing(a), "3508 WEDDINGTON RD, MATTHEWS NC 28105")
        self.assertIsNone(L.situs({}))


class ArcGisBaseTests(unittest.TestCase):
    def test_dates_and_strings(self):
        self.assertEqual(arcgis.epoch_ms_to_date(1681750800000), "2023-04-17")
        self.assertIsNone(arcgis.epoch_ms_to_date(None))
        self.assertIsNone(arcgis.epoch_ms_to_date("x"))
        self.assertEqual(arcgis.strip("  03302604       "), "03302604")
        self.assertIsNone(arcgis.strip("   "))
        self.assertEqual(arcgis.ring_centroid({"rings": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}), {"lon": 1.0, "lat": 1.0})
        self.assertIsNone(arcgis.ring_centroid(None))

    def test_fixture_paging_follows_exceeded_transfer_limit(self):
        c = L.ParcelXapoConnector(COUNTY, "fixture://parcel")
        pages = list(c.fetch(FixtureTransport(LIVE), None))
        self.assertEqual(len(pages), 4)
        self.assertTrue(pages[0].url.endswith("/0.json"))
        records = [r for p in pages for r in c.parse(p.body)]
        self.assertGreater(len(records), 250)
        self.assertTrue(all(r["pin"] for r in records))

    def test_live_url_and_where(self):
        c = L.CodeEnforcementLiveConnector(COUNTY)
        self.assertIn("gis.charlottenc.gov", c.endpoint)
        self.assertEqual(c.where(None), "1=1")
        self.assertIn("DateCreated >= TIMESTAMP '2026-08-01 00:00:00'", c.where("2026-08-01T05:00:00Z"))
        calls = []

        class T:
            def get(self, url, params=None):
                calls.append(url)
                return json.dumps({"features": [], "exceededTransferLimit": False}).encode(), "application/json"

        list(c.fetch(T(), "2026-08-01"))
        self.assertEqual(len(calls), 1)
        self.assertIn("resultOffset=0", calls[0])
        self.assertIn("/query?", calls[0])

    def test_arcgis_error_is_raised(self):
        c = L.LienConnector(COUNTY)
        with self.assertRaises(ValueError):
            c.parse(json.dumps({"error": {"code": 400, "message": "bad"}}).encode())

    def test_contract_goldens(self):
        for c in registry.connectors(COUNTY, "live", L.fixture_endpoints()):
            r = check(c, LIVE, os.path.join(LIVE, "golden", c.name + ".json"))
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["golden"], "ok", c.name)


class LiveProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = live_store()

    def test_counts_and_registry(self):
        s = self.store
        self.assertEqual(registry.profiles(COUNTY), ["default", "live", "sample"])
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM parcel"), 255)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'code_enforcement'"), 275)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'lien'"), 63)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'vacant_land'"), 40)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM geocode_cache"), 300)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM parcel WHERE lat IS NOT NULL"), 200)

    def test_real_owner_strings_parse(self):
        s = self.store
        rows = [json.loads(r["parsed"]) for r in s.query("SELECT parsed FROM mention WHERE role = 'owner' AND current = 1")]
        estates = [p for p in rows if p["markers"] and not p["is_organization"]]
        trusts = [p for p in rows if p.get("trust") and not p["is_organization"]]
        self.assertGreaterEqual(len(estates), 20)
        self.assertGreaterEqual(len(trusts), 15)
        self.assertTrue(all(p["first"] and p["last"] for p in estates + trusts))
        care_of = s.scalar("SELECT COUNT(*) FROM mention WHERE role = 'care_of' AND current = 1")
        self.assertGreaterEqual(care_of, 5)
        orgs = s.scalar("SELECT COUNT(*) FROM mention WHERE role = 'owner' AND is_organization = 1 AND current = 1")
        self.assertGreater(orgs, 20)

    def test_geocode_from_address_points(self):
        s = self.store
        pt = geocode.lookup(s, "3508 Weddington Rd, Matthews NC 28105")
        self.assertIsNotNone(pt)
        self.assertAlmostEqual(pt[0], 35.0788, places=2)
        parcel = entities.get_parcel(s, "MECKLENBURG/23125984")
        self.assertIsNotNone(parcel["lat"])

    def test_signal_stack_on_real_parcels(self):
        s = self.store
        rows = signals.rank(s, limit=30)
        self.assertTrue(rows)
        names = {x["signal"] for r in rows for x in r["signals"]}
        self.assertIn("estate_marker_on_owner", names)
        self.assertIn("city_lien", names)
        self.assertIn("code_case_open", names)
        self.assertIn("vacant_land", {x["signal"] for r in signals.rank(s, limit=300, min_score=1) for x in r["signals"]})
        top = rows[0]
        self.assertGreaterEqual(top["score"], 40)

    def test_events_from_live_sources(self):
        s = self.store
        kinds = {r["kind"] for r in s.query("SELECT DISTINCT kind FROM event")}
        for k in ("code_case_opened", "lien_recorded", "vacant_land_flagged"):
            self.assertIn(k, kinds)

    def test_daily_pipeline_live_profile(self):
        s = fresh_store("2026-09-04T00:00:00Z")
        report = pipeline.run_daily(s, COUNTY, FixtureTransport(LIVE), sender=lambda u, h, b: 200, profile="live", endpoints=L.fixture_endpoints())
        self.assertTrue(report["ok"], pipeline.summary(report))


if __name__ == "__main__":
    unittest.main()
