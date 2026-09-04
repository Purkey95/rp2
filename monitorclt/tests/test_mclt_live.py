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
        self.assertEqual(registry.profiles(COUNTY), ["live", "sample"])
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
        with open(os.path.join(LIVE, "address_point", "0.json"), encoding="utf-8") as f:
            first = json.load(f)["features"][0]
        addr, pid, geom = first["attributes"]["FullAddress"], first["attributes"]["TaxParcelID"], first["geometry"]
        pt = geocode.lookup(s, addr)
        self.assertIsNotNone(pt)
        self.assertAlmostEqual(pt[0], geom["y"], places=4)
        parcel = entities.get_parcel(s, "MECKLENBURG/" + pid)
        self.assertIsNotNone(parcel)
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


class DeedsIndexTests(unittest.TestCase):
    """Register of Deeds (Aumentum ROD Web Access) connector against captured result pages."""

    def test_fixture_pages_parse_unique_instruments(self):
        from monitorclt.sources import aumentum

        c = L.MecklenburgDeedConnector(COUNTY, "fixture://deed")
        pages = list(c.fetch(FixtureTransport(LIVE), None))
        self.assertEqual(len(pages), 3)
        recs = [r for p in pages for r in c.parse(p.body)]
        self.assertEqual(len(recs), 60)
        self.assertEqual(len({r["instrument_number"] for r in recs}), 60)
        self.assertTrue(all(r["recorded_date"] and r["book"] and r["page"] for r in recs))
        self.assertGreaterEqual(sum(1 for r in recs if r["parcel_pin"]), 45)
        self.assertTrue(all(r["grantor_name"] and r["grantee_name"] for r in recs))
        first = pages[0].body.decode("utf-8")
        self.assertEqual(aumentum.records_found(first), 168)
        self.assertEqual(aumentum.showing(first), (1, 20))
        self.assertEqual(aumentum.showing(pages[1].body.decode("utf-8")), (21, 40))

    def test_row_mapping_by_content(self):
        c = L.MecklenburgDeedConnector(COUNTY)
        cells = [
            "7",
            "View",
            "",
            "2026000001",
            "2026000001 40000- 12",
            "2026000001",
            "40000",
            "12",
            "09/02/2026",
            "EXTR EST",
            "EXTR EST",
            "[R] PUBLIC JOHN Q ESTATE (+) [E] PUBLIC JANE R",
            "R",
            "PUBLIC JOHN Q ESTATE (+)",
            "E",
            "PUBLIC JANE R",
            "LT 4 BLK 9 OAKVIEW PIN/PLSLIDE 069-133-10",
            "T",
            "Temp",
            "V",
            "N",
            "OPR1",
            "1",
            "0",
            "RE",
            "2",
            "1",
            "1",
            "",
            "P",
            "P",
        ]
        r = c.to_record(cells)
        self.assertEqual(
            (r["instrument_number"], r["book"], r["page"], r["recorded_date"], r["instrument_type"]), ("2026000001", "40000", "12", "2026-09-02", "EXTR EST")
        )
        self.assertEqual((r["grantor_name"], r["grantee_name"], r["parcel_pin"]), ("PUBLIC JOHN Q ESTATE", "PUBLIC JANE R", "069-133-10"))
        self.assertTrue(r["more_grantors"] and not r["more_grantees"])
        self.assertFalse(r["grantor_is_org"])
        self.assertIsNone(c.to_record(["header", "no numbers here"]))

    def test_search_protocol_encoding(self):
        import datetime as dt

        from monitorclt.sources import aumentum

        self.assertEqual(aumentum.date_state(dt.date(2026, 9, 2)), '|0|012026-9-2-0-0-0-0||[[[[]],[],[]],[{},[]],"012026-9-2-0-0-0-0"]')
        html = '<input type="hidden" name="__VIEWSTATE" value="abc" /><input type="checkbox" name="ctl00$cphNoMargin$f$dclDocType$50" value="DEED" /><input type="checkbox" name="ctl00$cphNoMargin$f$dclDocType$214" value="EXTR EST" />'
        self.assertEqual(aumentum.hidden_fields(html), {"__VIEWSTATE": "abc"})
        self.assertEqual(aumentum.doc_type_fields(html)["EXTR EST"], "ctl00$cphNoMargin$f$dclDocType$214")
        c = L.MecklenburgDeedConnector(COUNTY, today=dt.date(2026, 9, 4))
        self.assertEqual(c.date_range("2026-09-01"), (dt.date(2026, 8, 31), dt.date(2026, 9, 4)))
        self.assertEqual(c.date_range(None), (dt.date(2026, 8, 28), dt.date(2026, 9, 4)))

    def test_live_conversation_is_replayed_against_a_fake_site(self):
        """Drive the real fetch() through a scripted transport: disclaimer, form, search, paging."""
        import datetime as dt

        calls = []
        entry = ('<input type="hidden" name="__VIEWSTATE" value="v" /><input type="checkbox" name="ctl00$cphNoMargin$f$dclDocType$50" value="DEED" />').encode()
        with open(os.path.join(LIVE, "deed", "0.html"), "rb") as f:
            page1 = f.read()
        with open(os.path.join(LIVE, "deed", "1.html"), "rb") as f:
            page2 = f.read()

        class Site:
            def get(self, url, params=None):
                calls.append(("GET", url))
                if url.endswith("/RealEstate/SearchEntry.aspx"):
                    return entry, "text/html"
                if "pg=2" in url:
                    return page2, "text/html"
                if "pg=3" in url:
                    return b"<html>no rows</html>", "text/html"
                return b"<input type='hidden' name='__VIEWSTATE' value='h' />", "text/html"

            def post(self, url, fields, referer=None):
                calls.append(("POST", url, dict(fields)))
                if url.endswith("SearchEntry.aspx"):
                    return page1, "text/html"
                return b"", "text/html"

        c = L.MecklenburgDeedConnector(COUNTY, "https://rod.example.invalid", today=dt.date(2026, 9, 4))
        c.max_pages = 2
        pages = list(c.fetch(Site(), "2026-09-01"))
        self.assertEqual(len(pages), 2)
        posts = [x for x in calls if x[0] == "POST"]
        self.assertEqual(posts[0][2]["__EVENTTARGET"], "ctl00$cph1$lnkAccept")
        search = posts[1][2]
        self.assertEqual(search["__EVENTTARGET"], "ctl00$cphNoMargin$SearchButtons1$btnSearch")
        self.assertEqual(search["ctl00$cphNoMargin$f$dclDocType$50"], "DEED")
        self.assertIn("012026-8-31-0-0-0-0", search["cphNoMargin_f_ddcDateFiledFrom_clientState"])
        self.assertTrue(any("pg=2" in x[1] for x in calls if x[0] == "GET"))
        self.assertEqual(pages[0].watermark, "2026-09-04")

    def test_connectors_pace_themselves(self):
        import datetime as dt

        waits = []
        c = L.MecklenburgDeedConnector(COUNTY, "https://rod.example.invalid", today=dt.date(2026, 9, 4), sleep=waits.append)
        c.min_interval_s = 5.0
        c._last_request = 10**12  # pretend a request just happened far in the future so the next one must wait
        c._pace()
        self.assertTrue(waits and waits[0] > 0)
        a = L.ParcelXapoConnector(COUNTY, sleep=waits.append)
        a.min_interval_s = 5.0
        a._last_request = 10**12
        a._pace()
        self.assertEqual(len(waits), 2)

    def test_deed_source_in_live_profile(self):
        s = live_store()
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'deed'"), 60)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM mention WHERE source = 'deed' AND role = 'grantor' AND current = 1"), 40)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'deed_recorded' AND source = 'deed'"), 50)
