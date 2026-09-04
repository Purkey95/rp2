import unittest

from mclt_helpers import fresh_store, ingested, resolved

from monitorclt import review
from monitorclt.normalize.names import parse_name
from monitorclt.resolve import features as F
from monitorclt.resolve import gates as G
from monitorclt.resolve import resolve, resolver
from monitorclt.resolve.model import LinkModel, load_rules


def by_pair(store):
    return {(m["left_id"], m["right_id"]): m for m in resolver.matches(store)}


class ResolverRegressionTests(unittest.TestCase):
    """The v2 resolver must reproduce v1's dispositions on v1's sample, then do more."""

    def test_v1_sample_dispositions(self):
        s = resolved()
        m = by_pair(s)
        confirmed = sorted(k for k, v in m.items() if v["status"] == "confirmed")
        self.assertEqual(
            confirmed,
            [
                ("MECKLENBURG/26 E 001234", "MECKLENBURG/04512108"),
                ("MECKLENBURG/26 E 001235", "MECKLENBURG/21300244"),
                ("MECKLENBURG/26 E 001236", "MECKLENBURG/01745502"),
            ],
        )
        pending = sorted(k for k, v in m.items() if v["status"] == "pending")
        self.assertEqual(
            pending,
            [
                ("MECKLENBURG/26 E 001234", "MECKLENBURG/04513319"),
                ("MECKLENBURG/26 E 001237", "MECKLENBURG/09900101"),
                ("MECKLENBURG/26 E 001238", "MECKLENBURG/41288031"),
            ],
        )
        smiths = [v for k, v in m.items() if k[0].endswith("001237") and not k[1].endswith("09900101")]
        self.assertEqual(len(smiths), 7)
        self.assertTrue(all(v["status"] == "rejected" and "common_name" in v["evidence"] for v in smiths))
        self.assertEqual(m[("MECKLENBURG/26 E 001239", "UNION/07190225")]["status"], "rejected")
        self.assertIn("county_mismatch", m[("MECKLENBURG/26 E 001239", "UNION/07190225")]["evidence"])
        self.assertIn("post_death_conveyance", m[("MECKLENBURG/26 E 001236", "MECKLENBURG/01745502")]["flags"])
        self.assertIn("gate_failed:no_hard_conflict", m[("MECKLENBURG/26 E 001238", "MECKLENBURG/41288031")]["flags"])

    def test_new_evidence_kinds_fire(self):
        s = resolved()
        m = by_pair(s)
        home = m[("MECKLENBURG/26 E 001234", "MECKLENBURG/04512108")]
        self.assertIn("related_party_on_candidate", home["evidence"])  # the PR is a co-owner
        self.assertIn("deed_grantee_link", home["evidence"])
        self.assertIn("situs_address_match", m[("MECKLENBURG/26 E 001235", "MECKLENBURG/21300244")]["evidence"])

    def test_run_bookkeeping(self):
        s = ingested()
        r = resolve(s)
        c = r["counts"]
        self.assertEqual((c["subjects"], c["skipped"], c["blocked_out"], c["candidates"]), (8, 1, 1, 14))
        self.assertEqual(r["blocked_out"], ["MECKLENBURG/26 E 001240"])
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM person"), 3)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'match_confirmed'"), 3)
        # second run: same rows, no duplicate events, confirmed kept
        r2 = resolve(s)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM entity_match"), 14)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM event WHERE kind = 'match_confirmed'"), 3)
        self.assertEqual(r2["counts"]["kept_reviewed"], 3)

    def test_reviewer_decision_survives_rerun(self):
        s = resolved()
        pending = review.queue(s)[0]
        review.decide(s, pending["id"], "reject", "alice", "not him")
        resolve(s)
        m = resolver.get_match(s, pending["id"])
        self.assertEqual((m["status"], m["decided_by"], m["reviewer"]), ("rejected", "reviewer", "alice"))


class GateTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()

    def test_name_only_never_confirms(self):
        feats = {"name_full_exact": 1.0}
        gates = G.evaluate(feats, self.rules)
        status, flags = G.disposition(0.99, gates, feats, self.rules)
        self.assertEqual(status, "pending")
        self.assertIn("name_only_needs_human_review", flags)

    def test_org_and_county_never_confirm(self):
        for bad in ("organization_candidate", "county_mismatch"):
            feats = {"name_full_exact": 1.0, "estate_marker_on_candidate": 1.0, bad: 1.0}
            status, flags = G.disposition(0.99, G.evaluate(feats, self.rules), feats, self.rules)
            self.assertEqual(status, "pending", bad)
            self.assertTrue(any(f.startswith("gate_failed") for f in flags))

    def test_below_floor_rejects(self):
        feats = {"name_first_last_only": 1.0}
        self.assertEqual(G.disposition(0.1, G.evaluate(feats, self.rules), feats, self.rules)[0], "rejected")

    def test_tier(self):
        self.assertEqual(G.tier({"situs_address_match": 1, "deed_grantor_link": 1}, self.rules), "deed_grantor_link")


class FeatureAndModelTests(unittest.TestCase):
    def test_features_from_context(self):
        ctx = F.PairContext(
            subject=parse_name("John Q Public", "first_last"),
            candidate=parse_name("PUBLIC JOHN R", "last_first"),
            subject_record={"date_of_death": "2026-01-01"},
            candidate_record={},
            subject_county="MECKLENBURG",
            candidate_county="MECKLENBURG",
            subject_address="4210 ELM ST # 5 CHARLOTTE NC 28205",
            candidate_mailing="4210 ELM ST CHARLOTTE NC 28205",
            deeds_as_grantor=[{"recorded_date": "2026-02-01"}],
        )
        f = F.compute(ctx)
        self.assertEqual((f["middle_initial_conflict"], f["street_address_match"], f["deed_grantor_link"]), (1.0, 1.0, 1.0))
        self.assertEqual(F.flags_for(ctx, f), ["post_death_conveyance"])
        self.assertEqual(F.evidence_list(f)[0], "deed_grantor_link")

    def test_seed_model_matches_v1_ordering(self):
        m = LinkModel.seed(load_rules())
        p_marker = m.predict({"name_full_exact": 1, "estate_marker_on_candidate": 1})
        p_fl = m.predict({"name_first_last_only": 1})
        self.assertGreater(p_marker, 0.85)
        self.assertTrue(0.45 <= p_fl < 0.85)
        self.assertLess(m.predict({"name_full_exact": 1, "organization_candidate": 1}), 0.45)

    def test_training_shrinks_toward_seed_and_learns(self):
        rules = load_rules()
        seed = LinkModel.seed(rules)
        rows = [({"name_full_exact": 1, "situs_address_match": 1}, 1)] * 20 + [({"name_first_last_only": 1, "common_name": 1}, 0)] * 20
        model = LinkModel(seed.weights, seed.intercept)
        model.train(rows, seed, l2=1.0, lr=0.2, epochs=300)
        self.assertGreater(model.weights["situs_address_match"], seed.weights["situs_address_match"])
        self.assertLess(model.weights["common_name"], seed.weights["common_name"])
        # untouched features stay at their seed
        self.assertAlmostEqual(model.weights["suffix_conflict"], seed.weights["suffix_conflict"], places=3)

    def test_active_model_roundtrip(self):
        s = fresh_store()
        rules = load_rules()
        m = LinkModel({"a": 1.5}, -0.5)
        vid = m.save(s, 3, {"loss": 0.1})
        a = LinkModel.active(s, rules)
        self.assertEqual((a.version_id, a.weights, a.intercept), (vid, {"a": 1.5}, -0.5))


if __name__ == "__main__":
    unittest.main()
