import json
import os
import unittest

from mclt_helpers import resolved

from monitorclt import policy, signals, watch


class WatchlistTests(unittest.TestCase):
    def test_filters(self):
        ev = {"kind": "estate_opened", "county": "MECKLENBURG"}
        parcel = {"zip": "28205", "assessed_value": 300000, "land_use": "SFR", "lat": 35.13, "lon": -80.94}
        self.assertTrue(watch.matches_filter({}, ev, parcel))
        self.assertTrue(watch.matches_filter({"zips": ["28205"], "kinds": ["estate_opened"]}, ev, parcel))
        self.assertFalse(watch.matches_filter({"zips": ["28206"]}, ev, parcel))
        self.assertFalse(watch.matches_filter({"kinds": ["deed_recorded"]}, ev, parcel))
        self.assertFalse(watch.matches_filter({"counties": ["UNION"]}, ev, parcel))
        self.assertFalse(watch.matches_filter({"min_assessed_value": 500000}, ev, parcel))
        self.assertTrue(watch.matches_filter({"polygon": [[35.10, -80.95], [35.10, -80.90], [35.15, -80.90], [35.15, -80.95]]}, ev, parcel))
        self.assertFalse(watch.matches_filter({"radius": {"lat": 35.5, "lon": -80.0, "meters": 100}}, ev, parcel))
        self.assertFalse(watch.matches_filter({"zips": ["28205"]}, ev, None))

    def test_notifications_dedupe_and_go_through_policy(self):
        s = resolved()
        wid = watch.create_watchlist(s, "elm", "bob", {"zips": ["28205"]}, "webhook", "https://example.invalid/h", "env:MCLT_TEST_SECRET")
        r = watch.evaluate_watchlists(s)
        self.assertEqual(r["notifications_created"], 3)  # 2 deeds + 1 confirmed match on 28205 parcels
        self.assertEqual(watch.evaluate_watchlists(s)["notifications_created"], 0)
        kinds = sorted(n["payload"]["kind"] for n in watch.pending_notifications(s, wid))
        self.assertEqual(kinds, ["deed_recorded", "deed_recorded", "match_confirmed"])
        confirmed = [n for n in watch.pending_notifications(s, wid) if n["payload"]["kind"] == "match_confirmed"][0]
        self.assertEqual(confirmed["payload"]["detail"]["contact_role"], "personal_representative")
        deed = [n for n in watch.pending_notifications(s, wid) if n["payload"]["kind"] == "deed_recorded"][0]
        self.assertNotIn("grantor_name", deed["payload"]["detail"])  # no names outside the policy path

    def test_suppressed_person_never_reaches_a_webhook(self):
        s = resolved()
        policy.add_suppression(s, "person", "Jane R Public", "opt-out")
        watch.create_watchlist(s, "elm", "bob", {"zips": ["28205"], "kinds": ["match_confirmed"]}, "webhook", "https://x", "env:MCLT_TEST_SECRET")
        self.assertEqual(watch.evaluate_watchlists(s)["notifications_created"], 0)

    def test_signed_delivery(self):
        os.environ["MCLT_TEST_SECRET"] = "sekrit"
        s = resolved()
        wid = watch.create_watchlist(s, "elm", "bob", {"zips": ["28205"]}, "webhook", "https://example.invalid/h", "env:MCLT_TEST_SECRET")
        watch.evaluate_watchlists(s)
        sent = []

        def sender(url, headers, body):
            sent.append((url, headers, body))
            return 200

        out = watch.deliver(s, sender)
        self.assertEqual(out, [{"watchlist_id": wid, "sent": 3, "status": 200}])
        url, headers, body = sent[0]
        self.assertTrue(watch.verify("sekrit", body, headers["X-MonitorCLT-Signature"]))
        self.assertFalse(watch.verify("wrong", body, headers["X-MonitorCLT-Signature"]))
        self.assertEqual(len(json.loads(body)["notifications"]), 3)
        self.assertEqual(len(watch.pending_notifications(s, wid)), 0)
        # a failed delivery keeps notifications pending and is logged
        watch.create_watchlist(s, "all", "bob", {}, "webhook", "https://example.invalid/2", None)
        watch.evaluate_watchlists(s)
        out = watch.deliver(s, lambda u, h, b: 503, watchlist_id=wid + 1)
        self.assertEqual(out[0]["status"], 503)
        self.assertTrue(watch.pending_notifications(s, wid + 1))
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM export_log WHERE channel = 'webhook' AND allowed = 0"), 1)

    def test_digest_marks_delivered(self):
        s = resolved()
        wid = watch.create_watchlist(s, "elm", "bob", {"zips": ["28205"]})
        watch.evaluate_watchlists(s)
        text = watch.digest(s, wid)
        self.assertIn("MATCH CONFIRMED", text)
        self.assertIn("Jane R Public", text)
        self.assertEqual(len(watch.pending_notifications(s, wid)), 0)
        self.assertIn("(0 new)", watch.digest(s, wid))


class SignalTests(unittest.TestCase):
    def test_signal_stack_and_ranking(self):
        s = resolved()
        rows = signals.rank(s, limit=10)
        self.assertEqual(rows[0]["parcel_id"], "MECKLENBURG/13307012")
        top = {x["signal"]: x for x in rows[0]["signals"]}
        self.assertEqual(set(top), {"estate_marker_on_owner", "foreclosure_pending", "tax_delinquent", "code_case_open", "long_tenure"})
        self.assertEqual(top["tax_delinquent"]["weight"], 30)  # 3 years delinquent
        sycamore = next(r for r in rows if r["parcel_id"] == "MECKLENBURG/21300244")
        self.assertIn("estate_confirmed", {x["signal"] for x in sycamore["signals"]})
        self.assertIn("absentee_owner", {x["signal"] for x in sycamore["signals"]})
        self.assertTrue(all(rows[i]["score"] >= rows[i + 1]["score"] for i in range(len(rows) - 1)))
        text = signals.format_rank(rows[:2])
        self.assertIn("foreclosure_pending", text)

    def test_negative_signals(self):
        s = resolved()
        willow = s.one("SELECT * FROM parcel WHERE id = 'MECKLENBURG/01745502'")
        names = {x["signal"] for x in signals.parcel_signals(s, willow)}
        self.assertIn("post_death_conveyance", names)
        self.assertIn("estate_confirmed", names)

    def test_zip_filter(self):
        s = resolved()
        rows = signals.rank(s, zips=["28211"])
        self.assertEqual(rows[0]["parcel_id"], "MECKLENBURG/13307012")
        self.assertTrue(all(r["zip"] == "28211" for r in rows))


if __name__ == "__main__":
    unittest.main()
