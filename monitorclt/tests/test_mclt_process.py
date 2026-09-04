"""Tests for the process improvements: wider blocking, trusts, geo/time features, person
clustering, estate-centric review, audit and double review, outcomes, cadence, the
business registry, secret references, and the daily pipeline."""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from mclt_helpers import COUNTY, FIXTURES, fresh_store, ingested, resolved

from monitorclt import api, entities, geocode, ingest, outcomes, persons, pipeline, policy, review, signals, watch
from monitorclt.counties import mecklenburg
from monitorclt.normalize import names
from monitorclt.normalize.names import parse_name
from monitorclt.resolve import features as F
from monitorclt.resolve import gates as G
from monitorclt.resolve import resolve, resolver
from monitorclt.resolve.model import load_rules
from monitorclt.sources.transport import FixtureTransport


def parcel_row(pin, owner, situs, mailing=None, **extra):
    row = {
        "county": COUNTY,
        "pin": pin,
        "owner_name": owner,
        "situs_address": situs,
        "owner_mailing_address": mailing or situs,
        "land_use": "SFR",
        "assessed_value": 250000,
    }
    row.update(extra)
    return row


def estate_row(file_number, decedent, pr, address, death="2026-01-10", filed="2026-01-25"):
    return {
        "county": COUNTY,
        "file_number": file_number,
        "decedent_name": decedent,
        "date_of_death": death,
        "filing_date": filed,
        "case_status": "OPEN",
        "personal_rep_name": pr,
        "pr_mailing_address": address,
    }


def small_world(estates, parcels, deeds=None):
    """A store with just these estates and parcels ingested and resolved."""
    s = fresh_store()
    bodies = {
        "estates.json": json.dumps(estates).encode(),
        "parcels.jsonl": "\n".join(json.dumps(p) for p in parcels).encode(),
        "deeds.jsonl": "\n".join(json.dumps(d) for d in (deeds or [])).encode(),
    }
    t = FixtureTransport(bodies=bodies)
    assert ingest.ingest(s, mecklenburg.EstateCaseConnector(endpoint="fixture://estates.json"), t).ok
    assert ingest.ingest(s, mecklenburg.ParcelConnector(), t).ok
    assert ingest.ingest(s, mecklenburg.DeedConnector(), t).ok
    return s


class TrustAndBlockingTests(unittest.TestCase):
    def test_trust_strings_yield_a_person(self):
        n = parse_name("PUBLIC JOHN Q TRUSTEE", "last_first")
        self.assertEqual((n.first, n.last, n.trust, n.is_organization), ("JOHN", "PUBLIC", True, False))
        n = parse_name("JOHN Q PUBLIC REVOCABLE LIVING TRUST", "last_first")
        self.assertEqual((n.first, n.middle, n.last, n.order_uncertain), ("JOHN", "Q", "PUBLIC", True))
        self.assertIn("PUBLIC|JOHN", names.block_keys(n))
        n = parse_name("PUBLIC FAMILY REVOCABLE TRUST", "last_first")
        self.assertTrue(n.is_organization and n.trust)
        parties = names.split_parties("PUBLIC JOHN Q & JANE R TTEES", "last_first")
        self.assertEqual([(p.first, p.last, p.trust) for p in parties], [("JOHN", "PUBLIC", True), ("JANE", "PUBLIC", True)])

    def test_relations_and_keys(self):
        self.assertEqual(names.first_name_relation("BILL", "WILLIAM"), "nickname")
        self.assertEqual(names.first_name_relation("J", "JOHN"), "initial")
        self.assertEqual(names.first_name_relation("JOHN", "JANE"), "different")  # no forename phonetics, on purpose
        self.assertEqual(names.last_name_relation("SMYTHE", "SMITH"), "phonetic")
        keys = names.block_keys(parse_name("SMITH BILL", "last_first"))
        self.assertIn("WILLIAM|SMITH", keys)
        self.assertIn("B.|SMITH", keys)
        self.assertIn("~WILLIAM|S530", keys)
        self.assertEqual(names.canonical_first("CATHY"), "KATHERINE")

    def test_trust_owned_parcel_reaches_the_queue(self):
        s = small_world(
            [estate_row("26 E 9", "John Q Public", "Jane Public", "1 Elm St, Charlotte NC 28205")],
            [parcel_row("1", "JOHN Q PUBLIC REVOCABLE LIVING TRUST", "1 Elm St, Charlotte NC 28205")],
        )
        r = resolve(s)
        self.assertEqual(r["counts"]["candidates"], 1)
        m = resolver.matches(s)[0]
        self.assertIn("trust_owner", m["evidence"])
        self.assertIn("trust_held", m["flags"])
        self.assertIn("mailing_address_match", m["evidence"])

    def test_nickname_and_initial_and_phonetic_widen_but_stay_gated(self):
        s = small_world(
            [
                estate_row("26 E 1", "William Smith", "Ann Smith", "10 Oak St, Charlotte NC 28205"),
                estate_row("26 E 2", "Robert Jones", "Ann Jones", "20 Oak St, Charlotte NC 28205"),
                estate_row("26 E 3", "Henry Smythe", "Ann Smythe", "30 Oak St, Charlotte NC 28205"),
            ],
            [
                parcel_row("1", "SMITH BILL", "10 Oak St, Charlotte NC 28205"),
                parcel_row("2", "JONES R", "20 Oak St, Charlotte NC 28205"),
                parcel_row("3", "SMITH HENRY", "30 Oak St, Charlotte NC 28205"),
            ],
        )
        r = resolve(s)
        self.assertEqual(r["counts"]["blocked_out"], 0)
        self.assertEqual(r["counts"]["widened"], 3)
        m = {x["left_id"].split("/")[1]: x for x in resolver.matches(s)}
        self.assertIn("name_nickname_match", m["26 E 1"]["evidence"])
        self.assertIn("name_initial_only", m["26 E 2"]["evidence"])
        self.assertIn("name_phonetic_match", m["26 E 3"]["evidence"])
        for k in ("26 E 2", "26 E 3"):
            self.assertNotEqual(m[k]["status"], "confirmed", k)
            self.assertIn("weak_name_match", m[k]["flags"])
            self.assertFalse(m[k]["gates"]["no_weak_name_only"])

    def test_forename_collision_is_not_a_candidate(self):
        s = small_world(
            [estate_row("26 E 1", "John Q Public", "X Y", "1 Elm St, Charlotte NC 28205")], [parcel_row("1", "PUBLIC JANE R", "1 Elm St, Charlotte NC 28205")]
        )
        r = resolve(s)
        self.assertEqual((r["counts"]["candidates"], r["counts"]["blocked_out"]), (0, 1))

    def test_best_party_per_record_wins(self):
        s = small_world(
            [estate_row("26 E 1", "John Q Public", "Jane R Public", "1 Elm St, Charlotte NC 28205")],
            [parcel_row("1", "PUBLIC JANE R & JOHN Q", "1 Elm St, Charlotte NC 28205")],
        )
        resolve(s)
        m = resolver.matches(s)
        self.assertEqual(len(m), 1)
        self.assertIn("name_full_exact", m[0]["evidence"])
        self.assertIn("related_party_on_candidate", m[0]["evidence"])


class TimeAndGeoFeatureTests(unittest.TestCase):
    def test_sale_after_death_is_negative_and_flagged(self):
        s = small_world(
            [estate_row("26 E 1", "John Q Public", "Jane Public", "PO Box 9, Charlotte NC 28201", death="2026-01-10")],
            [parcel_row("1", "PUBLIC JOHN Q", "1 Elm St, Charlotte NC 28205", last_sale_date="2026-03-01")],
        )
        resolve(s)
        m = resolver.matches(s)[0]
        self.assertIn("sale_after_death", m["evidence"])
        self.assertIn("sale_after_death", m["flags"])
        self.assertLess(m["probability"], 0.45)

    def test_coordinates_match_when_text_does_not(self):
        s = small_world(
            [estate_row("26 E 1", "John Q Public", "Jane Public", "Elm Street Cottage, Charlotte NC 28205")],
            [parcel_row("1", "PUBLIC JOHN Q", "1 Elm St, Charlotte NC 28205", "PO BOX 1 CHARLOTTE NC 28201", lat=35.2, lon=-80.8)],
        )
        provider = geocode.StaticGeocoder({"Elm Street Cottage, Charlotte NC 28205": (35.2001, -80.8001)})
        resolve(s, geocoder=provider)
        m = resolver.matches(s)[0]
        self.assertIn("coordinates_match", m["evidence"])
        self.assertEqual(m["status"], "pending")  # geometry plus a name is a question for a human, not a confirmation
        self.assertGreater(m["probability"], 0.75)
        self.assertEqual(s.scalar("SELECT provider FROM geocode_cache"), "static")
        # cache-only lookup afterwards does not call the provider
        self.assertEqual(geocode.lookup(s, "Elm Street Cottage, Charlotte NC 28205"), (35.2001, -80.8001))
        self.assertEqual(geocode.warm_from_parcels(s), 1)

    def test_weak_name_gate(self):
        rules = load_rules()
        feats = {"name_initial_only": 1.0, "mailing_address_match": 1.0}
        self.assertFalse(G.evaluate(feats, rules)["no_weak_name_only"])
        feats["deed_grantor_link"] = 1.0
        self.assertTrue(G.evaluate(feats, rules)["no_weak_name_only"])
        self.assertTrue(F.names_related(parse_name("John Public", "first_last"), parse_name("PUBLIC J", "last_first")))


class PersonClusterTests(unittest.TestCase):
    def test_cluster_attaches_deed_and_tax_mentions(self):
        s = resolved()
        before = s.scalar("SELECT COUNT(*) FROM mention WHERE person_id IS NOT NULL")
        counts = persons.cluster_persons(s)
        self.assertGreater(counts["attached"], 0)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM mention WHERE person_id IS NOT NULL"), before)
        prof = persons.profile(s, 1)
        self.assertIn("deed", prof["sources"])
        self.assertTrue(prof["parcels"])
        self.assertTrue(prof["events"])
        # a David Smith at a different address is never merged by name alone
        smith = s.one("SELECT id FROM person WHERE normalized_name LIKE 'DAVID%'")
        self.assertIsNone(smith)
        self.assertIsNone(persons.profile(s, 999))


class ReviewProcessTests(unittest.TestCase):
    def test_orders(self):
        s = resolved()
        by_p = [m["id"] for m in review.queue(s, order="probability")]
        by_u = [m["id"] for m in review.queue(s, order="uncertainty")]
        by_v = review.queue(s, order="value")
        self.assertEqual(by_p, sorted(by_p, key=lambda i: -resolver.get_match(s, i)["probability"]))
        self.assertEqual(by_u[0], 13)  # p=0.45 is the coin flip
        self.assertTrue(all(by_v[i]["review_value"] >= by_v[i + 1]["review_value"] for i in range(len(by_v) - 1)))
        with self.assertRaises(ValueError):
            review.queue(s, order="random")

    def test_groups_and_decide_group(self):
        s = resolved()
        gs = review.groups(s)
        smith = next(g for g in gs if g["left_id"].endswith("001237"))
        self.assertEqual(len(smith["candidates"]), 8)
        self.assertEqual(smith["subject"]["decedent_name"], "David Smith")
        self.assertTrue(all("parcel" in c for c in smith["candidates"]))
        r = review.decide_group(s, smith["left_id"], ["MECKLENBURG/09900101"], "alice", "his home")
        self.assertEqual(r["confirmed"], ["MECKLENBURG/09900101"])
        self.assertEqual(resolver.matches(s, left_id=smith["left_id"], status="pending"), [])
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM label WHERE is_match = 1"), 1)
        john = next(g for g in review.groups(s) if g["left_id"].endswith("001234"))
        r = review.decide_group(s, john["left_id"], [], "alice")
        self.assertEqual(r["rejected"], ["MECKLENBURG/04513319"])
        with self.assertRaises(ValueError):
            review.decide_group(s, john["left_id"], ["NOPE/1"], "alice")
        with self.assertRaises(KeyError):
            review.decide_group(s, "NOPE/0", [], "alice")

    def test_audit_and_double_review_sampling(self):
        s = ingested()
        rules = load_rules()
        rules["review"] = {"audit_sample_rate": 1.0, "double_review_rate": 1.0}
        resolve(s, rules=rules)
        audit = review.audit_queue(s)
        self.assertEqual(len(audit), 3)
        self.assertTrue(all("audit_sample" in m["flags"] and m["status"] == "confirmed" for m in audit))
        review.decide(s, audit[0]["id"], "confirm", "alice")
        self.assertEqual(len(review.audit_queue(s)), 2)
        pend = review.queue(s)
        self.assertTrue(all("double_review" in m["flags"] for m in pend))
        self.assertEqual(review.double_review_queue(s, "bob"), [])  # nobody has decided yet
        review.decide(s, pend[0]["id"], "confirm", "alice")
        dq = review.double_review_queue(s, "bob")
        self.assertEqual([m["id"] for m in dq], [pend[0]["id"]])
        self.assertEqual(review.double_review_queue(s, "alice"), [])
        review.decide(s, pend[0]["id"], "reject", "bob")
        rep = review.agreement_report(s)
        self.assertEqual((rep["double_reviewed"], rep["comparisons"], rep["agreement"]), (1, 1, 0.0))
        self.assertEqual(rep["disagreements"][0]["match_id"], pend[0]["id"])
        review.decide(s, pend[1]["id"], "reject", "alice")
        review.decide(s, pend[1]["id"], "reject", "bob")
        self.assertEqual(review.agreement_report(s)["agreement"], 0.5)


class OutcomeAndCadenceTests(unittest.TestCase):
    def test_declined_suppresses_and_blocks_export(self):
        s = resolved()
        rows = policy.export(s, "alice")["rows"]
        r = outcomes.record_outcome(s, "declined", "alice", lead_id=rows[0]["lead_id"], note="asked not to be contacted")
        self.assertEqual(sorted(r["effects"]), ["suppressed address", "suppressed parcel", "suppressed person"])
        s.clock.advance(days=40)  # past the cadence window
        d = policy.check_export(s, r["match_id"], "alice", "csv")
        self.assertFalse(d.allowed)
        self.assertTrue(any(x.startswith("suppressed:") for x in d.reasons))
        self.assertIn("outcome:declined", d.reasons)
        with self.assertRaises(ValueError):
            outcomes.record_outcome(s, "shrug", "alice", match_id=r["match_id"])
        with self.assertRaises(KeyError):
            outcomes.record_outcome(s, "reached", "alice", lead_id="nope")

    def test_negative_outcomes_become_parcel_signals(self):
        s = resolved()
        rows = policy.export(s, "alice")["rows"]
        outcomes.record_outcome(s, "not_in_estate", "alice", lead_id=rows[1]["lead_id"])
        parcel = entities.get_parcel(s, rows[1]["parcel_id"])
        names_ = {x["signal"] for x in signals.parcel_signals(s, parcel)}
        self.assertIn("outcome_not_in_estate", names_)
        rep = outcomes.conversion_report(s)
        self.assertEqual(rep["totals"]["negative"], 1)
        self.assertIn("outreach outcomes", outcomes.format_conversion(rep))

    def test_cadence(self):
        s = resolved()
        first = policy.export(s, "alice")
        self.assertEqual(len(first["rows"]), 3)
        second = policy.export(s, "alice")
        self.assertEqual(len(second["rows"]), 0)
        self.assertTrue(all(any(r.startswith("cadence_exceeded") for r in d["reasons"]) for d in second["denied"]))
        s.clock.advance(days=31)
        self.assertEqual(len(policy.export(s, "alice")["rows"]), 3)


class BusinessEntityTests(unittest.TestCase):
    def test_registry_enriches_entity_owner(self):
        s = resolved()
        parcel = entities.get_parcel(s, "MECKLENBURG/13307013")
        sigs = {x["signal"]: x for x in signals.parcel_signals(s, parcel)}
        self.assertEqual(sigs["entity_owner"]["detail"]["registered_agent_name"], "Peter Example")
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'entity_registered'"), 3)
        self.assertIsNotNone(signals.business_entity_for(s, "ANYCORP BUILDERS LLC"))
        self.assertIsNone(signals.business_entity_for(s, ""))
        # a dissolved owner is its own signal
        s.execute("UPDATE parcel SET owner_string = 'ANYCORP BUILDERS LLC' WHERE id = 'MECKLENBURG/13307013'")
        s.execute(
            "UPDATE mention SET parsed = replace(parsed, 'NONESUCH HOLDINGS LLC', 'ANYCORP BUILDERS LLC') WHERE parcel_id = 'MECKLENBURG/13307013' AND source = 'parcel'"
        )
        sigs = {x["signal"] for x in signals.parcel_signals(s, parcel)}
        self.assertIn("entity_dissolved", sigs)


class SecretsAndPipelineTests(unittest.TestCase):
    def test_secret_references(self):
        s = fresh_store()
        with self.assertRaises(ValueError):
            watch.create_watchlist(s, "w", "o", {}, "webhook", "https://x", "plaintext")
        os.environ["MCLT_T2"] = "zzz"
        wid = watch.create_watchlist(s, "w", "o", {}, "webhook", "https://x", "env:MCLT_T2")
        self.assertEqual(watch.watchlists(s)[0]["secret_kind"], "env")
        self.assertNotIn("secret", watch.watchlists(s)[0])
        headers, body = watch.webhook_request({"name": "w", "secret": "env:MCLT_T2"}, [{"payload": {"kind": "x"}}], "now")
        self.assertTrue(watch.verify("zzz", body, headers["X-MonitorCLT-Signature"]))
        self.assertEqual(watch.resolve_secret("env:MCLT_MISSING_XYZ"), None)
        self.assertEqual(wid, 1)

    def test_run_daily_ok_and_failure(self):
        s = fresh_store()
        policy.default_retention(s)
        report = pipeline.run_daily(s, COUNTY, FixtureTransport(FIXTURES), sender=lambda u, h, b: 200)
        self.assertTrue(report["ok"], pipeline.summary(report))
        self.assertEqual([x["step"] for x in report["steps"]], ["ingest", "resolve", "cluster", "watchlists", "deliver", "status"])
        text = pipeline.summary(report)
        self.assertIn("OK", text)
        alerts = []
        s.clock.advance(hours=1)
        report = pipeline.run_daily(
            s, COUNTY, FixtureTransport(bodies={}), sender=lambda u, h, b: alerts.append(u) or 200, alert_webhook="https://alert.invalid/"
        )
        self.assertFalse(report["ok"])
        self.assertTrue(report["alerted"])
        self.assertEqual(alerts, ["https://alert.invalid/"])
        self.assertIn("FAILED", pipeline.summary(report))


class ProcessApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = resolved()
        cls.server = api.make_server(cls.store, "127.0.0.1", 0, trust_proxy_header="x-forwarded-user")
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, method="GET", body=None, user="alice"):
        headers = {"Content-Type": "application/json"}
        if user:
            headers["X-Forwarded-User"] = user
        req = urllib.request.Request(
            "http://127.0.0.1:{0}{1}".format(self.port, path), data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_proxy_identity_required(self):
        self.assertEqual(self.call("/api/status", user=None)[0], 401)

    def test_group_review_flow(self):
        status, g = self.call("/api/review/groups?limit=5")
        self.assertEqual(status, 200)
        smith = next(x for x in g["groups"] if x["left_id"].endswith("001237"))
        status, r = self.call("/api/review/groups/decide", "POST", {"left_id": smith["left_id"], "confirm": ["MECKLENBURG/09900101"]})
        self.assertEqual((status, r["confirmed"]), (200, ["MECKLENBURG/09900101"]))
        m = self.store.one("SELECT reviewer, status FROM entity_match WHERE right_id = 'MECKLENBURG/09900101'")
        self.assertEqual((m["reviewer"], m["status"]), ("alice", "confirmed"))  # identity came from the proxy header
        self.assertEqual(self.call("/api/review/groups/decide", "POST", {"confirm": []})[0], 400)
        status, q = self.call("/api/review/queue?order=uncertainty")
        self.assertEqual((status, q["order"]), (200, "uncertainty"))
        self.assertEqual(self.call("/api/review/audit")[0], 200)
        self.assertEqual(self.call("/api/review/double")[0], 200)
        self.assertEqual(self.call("/api/review/agreement")[0], 200)

    def test_outcomes_and_person(self):
        status, ex = self.call("/api/export?actor=alice")
        lead = ex["rows"][0]["lead_id"]
        status, r = self.call("/api/outcomes", "POST", {"lead_id": lead, "outcome": "reached", "note": "left voicemail"})
        self.assertEqual((status, r["outcome"]), (201, "reached"))
        self.assertEqual(self.call("/api/outcomes", "POST", {"lead_id": lead, "outcome": "nah"})[0], 400)
        status, rep = self.call("/api/outcomes/report")
        self.assertEqual(rep["totals"]["positive"], 1)
        status, p = self.call("/api/persons/1")
        self.assertIn("parcels", p)
        self.assertIn("sources", p)


if __name__ == "__main__":
    unittest.main()
