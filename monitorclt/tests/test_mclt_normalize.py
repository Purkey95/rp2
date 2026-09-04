import unittest

from mclt_helpers import fresh_store  # noqa: F401  (sets sys.path)

from monitorclt.normalize import addresses, geo, names, pins


class NameTests(unittest.TestCase):
    def test_assessor_order_and_shared_surname(self):
        parties = names.split_parties("PUBLIC JOHN Q & JANE R", "last_first")
        self.assertEqual([(p.first, p.middle, p.last) for p in parties], [("JOHN", "Q", "PUBLIC"), ("JANE", "R", "PUBLIC")])

    def test_court_order(self):
        n = names.parse_name("Robert Lee Testcase", "first_last")
        self.assertEqual((n.first, n.middle, n.last), ("ROBERT", "LEE", "TESTCASE"))
        self.assertEqual(n.key_fl, "ROBERT|TESTCASE")

    def test_comma_wins_and_suffix(self):
        n = names.parse_name("PUBLIC, JOHN Q JR", "first_last")
        self.assertEqual((n.first, n.middle, n.last, n.suffix), ("JOHN", "Q", "PUBLIC", "JR"))

    def test_estate_of_flips_order_and_marks(self):
        n = names.parse_name("ESTATE OF HENRY W PLACEHOLDER", "last_first")
        self.assertEqual((n.first, n.last), ("HENRY", "PLACEHOLDER"))
        self.assertIn("ESTATE OF", n.markers)

    def test_heirs_marker(self):
        n = names.parse_name("SAMPLE MARY A HEIRS", "last_first")
        self.assertEqual((n.first, n.middle, n.last), ("MARY", "A", "SAMPLE"))
        self.assertEqual(n.markers, ["HEIRS"])

    def test_organization(self):
        n = names.parse_name("NONESUCH HOLDINGS LLC", "last_first")
        self.assertTrue(n.is_organization)
        self.assertFalse(n.is_person)

    def test_agreement_and_conflicts(self):
        a = names.parse_name("John Q Public", "first_last")
        self.assertEqual(names.name_agreement(a, names.parse_name("PUBLIC JOHN QUINCY", "last_first")), "full")
        self.assertEqual(names.name_agreement(a, names.parse_name("PUBLIC JOHN", "last_first")), "first_last")
        self.assertEqual(names.name_agreement(a, names.parse_name("PUBLIC JOHN R", "last_first")), "conflict")
        self.assertTrue(names.suffix_conflict(names.parse_name("PUBLIC JOHN Q JR"), names.parse_name("PUBLIC JOHN Q SR")))

    def test_compound_surname_stays_one_token(self):
        n = names.parse_name("O'Brien-Smith, Mary", "last_first")
        self.assertEqual(n.last, "OBRIENSMITH")


class AddressTests(unittest.TestCase):
    def test_components(self):
        a = addresses.parse_address("4210 Elm Street Apt 5, Charlotte NC 28205-1234")
        self.assertEqual((a.number, a.street, a.suffix, a.unit, a.city, a.state, a.zip), ("4210", "ELM", "ST", "5", "CHARLOTTE", "NC", "28205"))
        self.assertEqual(a.normalized, "4210 ELM ST # 5 CHARLOTTE NC 28205")

    def test_directionals_and_suite(self):
        a = addresses.parse_address("100 N Tryon St Ste 200 Charlotte NC 28202")
        self.assertEqual((a.predir, a.street, a.suffix, a.unit), ("N", "TRYON", "ST", "200"))

    def test_po_box_and_care_of(self):
        self.assertTrue(addresses.parse_address("PO BOX 4471 CHARLOTTE NC 28204").is_po_box)
        a = addresses.parse_address("C/O THOMAS SAMPLE 1200 PINE ST MATTHEWS NC 28105")
        self.assertEqual(a.street_line, "1200 PINE ST")

    def test_compare_levels(self):
        self.assertEqual(addresses.compare("4210 Elm Street Apt 5, Charlotte NC 28205", "4210 ELM ST APT 5 CHARLOTTE NC 28205"), "exact")
        self.assertEqual(addresses.compare("4210 Elm Street Apt 5, Charlotte NC 28205", "4210 Elm St, Charlotte NC 28205"), "street")
        self.assertEqual(addresses.compare("4210 Elm St, Charlotte NC 28205", "9 Birch Ln, Charlotte NC 28205"), "none")
        self.assertEqual(addresses.compare("", "9 Birch Ln"), "none")

    def test_state_and_zip_helpers(self):
        self.assertEqual(addresses.state_of("1 Main St, Rock Hill SC 29730"), "SC")
        self.assertEqual(addresses.zip_of("1 Main St, Rock Hill SC 29730"), "29730")


class PinTests(unittest.TestCase):
    def test_norm_and_id(self):
        self.assertEqual(pins.pin_norm("045-121-08"), "04512108")
        self.assertEqual(pins.parcel_id("Mecklenburg County", "045.121.08"), "MECKLENBURG/04512108")

    def test_lineage(self):
        store = fresh_store()
        for pid in ("C/A", "C/B", "C/C", "C/D"):
            store.insert("parcel", {"id": pid, "county": "C", "pin": pid[2:], "pin_norm": pid[2:], "first_seen": "x", "last_seen": "x"})
        pins.record_lineage(store, "C/A", "C/B", "split")
        pins.record_lineage(store, "C/A", "C/C", "split")
        pins.record_lineage(store, "C/C", "C/D", "renumber")
        self.assertEqual(pins.resolve_current(store, "C/A"), ["C/B", "C/D"])
        self.assertEqual(pins.ancestry(store, "C/D"), ["C/A", "C/C", "C/D"])
        self.assertEqual(store.scalar("SELECT superseded_by FROM parcel WHERE id = 'C/C'"), "C/D")


class GeoTests(unittest.TestCase):
    def test_haversine_and_radius(self):
        d = geo.haversine_m((35.2271, -80.8431), (35.2271, -80.8331))
        self.assertTrue(880 < d < 930)
        self.assertTrue(geo.within_m((35.2271, -80.8431), (35.2271, -80.8331), 1000))
        self.assertFalse(geo.within_m(None, (0, 0), 1000))

    def test_polygon(self):
        square = [(35.10, -80.95), (35.10, -80.90), (35.15, -80.90), (35.15, -80.95)]
        self.assertTrue(geo.point_in_polygon((35.13, -80.94), square))
        self.assertFalse(geo.point_in_polygon((35.20, -80.94), square))
        self.assertTrue(geo.in_bbox((35.13, -80.94), geo.bbox(square)))


if __name__ == "__main__":
    unittest.main()
