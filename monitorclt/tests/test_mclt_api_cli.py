import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from mclt_helpers import FIXTURES, resolved

from monitorclt import api, cli


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = resolved()
        cls.server = api.make_server(cls.store, "127.0.0.1", 0, token="t0k")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, method="GET", body=None, token="t0k"):
        req = urllib.request.Request(
            "http://127.0.0.1:{0}{1}".format(self.port, path),
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-MonitorCLT-Token": token, "Content-Type": "application/json", "X-Reviewer": "alice"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_auth(self):
        self.assertEqual(self.call("/api/status", token="nope")[0], 401)

    def test_ui_and_routes(self):
        status, body = self.call("/review")
        self.assertEqual(status, 200)
        self.assertIn(b"MonitorCLT", body)
        self.assertEqual(self.call("/nope")[0], 404)
        self.assertEqual(self.call("/api/matches/999")[0], 404)

    def test_review_flow(self):
        status, q = self.call("/api/review/queue")
        self.assertEqual(status, 200)
        mid = q["queue"][0]["id"]
        status, d = self.call("/api/matches/{0}".format(mid))
        self.assertIn("contributions", d)
        status, r = self.call("/api/matches/{0}/decision".format(mid), "POST", {"decision": "confirm", "note": "ok"})
        self.assertEqual((status, r["match"]["status"], r["match"]["reviewer"]), (200, "confirmed", "alice"))
        self.assertEqual(self.call("/api/matches/{0}/decision".format(mid), "POST", {"decision": "bogus"})[0], 400)

    def test_entities_events_rank_export(self):
        status, p = self.call("/api/parcels/MECKLENBURG/045-121-08")
        self.assertEqual(status, 200)
        self.assertIn("estate_confirmed", [x["signal"] for x in p["signals"]])
        self.assertTrue(p["history"])
        status, person = self.call("/api/persons/1")
        self.assertEqual(status, 200)
        self.assertTrue(person["mentions"])
        status, ev = self.call("/api/events?kind=match_confirmed")
        self.assertGreaterEqual(len(ev["events"]), 3)
        status, rk = self.call("/api/rank?limit=3")
        self.assertEqual(len(rk["rank"]), 3)
        status, ex = self.call("/api/export?actor=alice")
        self.assertGreaterEqual(len(ex["rows"]), 3)
        status, trail = self.call("/api/leads/{0}/provenance".format(ex["rows"][0]["lead_id"]))
        self.assertEqual(status, 200)
        self.assertIn("raw_capture", trail)
        status, w = self.call("/api/watchlists", "POST", {"name": "w", "filters": {"zips": ["28205"]}})
        self.assertEqual(status, 201)
        status, dg = self.call("/api/watchlists/{0}/digest".format(w["id"]))
        self.assertIn("digest", dg)
        status, st = self.call("/api/status")
        self.assertIn("connectors", st)
        status, m = self.call("/api/model")
        self.assertIn("weights", m["model"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mclt-")
        self.db = os.path.join(self.tmp, "t.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return cli.main(["--db", self.db] + list(args))

    def test_pipeline_via_cli(self):
        self.assertEqual(self.run_cli("init"), 0)
        self.assertEqual(self.run_cli("contract", "--county", "MECKLENBURG", "--fixtures", FIXTURES, "--golden"), 0)
        self.assertEqual(self.run_cli("ingest", "--county", "MECKLENBURG", "--fixtures", FIXTURES), 0)
        self.assertEqual(self.run_cli("resolve"), 0)
        self.assertEqual(self.run_cli("review-queue"), 0)
        self.assertEqual(self.run_cli("import-labels", os.path.join(FIXTURES, "labels.csv")), 0)
        self.assertEqual(self.run_cli("train"), 0)
        self.assertEqual(self.run_cli("evaluate"), 0)
        out = os.path.join(self.tmp, "leads.csv")
        self.assertEqual(self.run_cli("export", "--actor", "alice", "--csv", out), 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 4)
        self.assertEqual(self.run_cli("rank", "--limit", "3"), 0)
        self.assertEqual(self.run_cli("status"), 0)
        self.assertEqual(self.run_cli("watchlist", "create", "--name", "z", "--filters", '{"zips":["28205"]}'), 0)
        self.assertEqual(self.run_cli("watchlist", "digest", "--id", "1"), 0)
        self.assertEqual(self.run_cli("suppress", "person", "Jane R Public", "--reason", "opt-out"), 0)
        self.assertEqual(self.run_cli("purge"), 0)
        self.assertEqual(self.run_cli("provenance", "nope"), 1)

    def test_endpoint_override_imports_legacy_jsonl(self):
        legacy = os.path.normpath(os.path.join(FIXTURES, "..", "..", "..", "tools", "monitorclt_probate", "sample"))
        if not os.path.isdir(legacy):
            self.skipTest("legacy sample not present")
        self.assertEqual(self.run_cli("init"), 0)
        rc = self.run_cli(
            "ingest",
            "--county",
            "MECKLENBURG",
            "--fixtures",
            legacy,
            "--source",
            "estate_case",
            "--source",
            "parcel",
            "--source",
            "deed",
            "--endpoint",
            "estate_case=fixture://estate_cases.jsonl",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.run_cli("resolve"), 0)


if __name__ == "__main__":
    unittest.main()
