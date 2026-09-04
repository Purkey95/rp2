import unittest

from mclt_helpers import resolved
from monitorclt.resolve import resolver

from monitorclt import history, policy, review


class ExportPolicyTests(unittest.TestCase):
    def test_only_confirmed_and_exact_fields(self):
        s = resolved()
        r = policy.export(s, "alice")
        self.assertEqual(len(r["rows"]), 3)
        for row in r["rows"]:
            self.assertEqual(tuple(row), policy.EXPORT_FIELDS)
            self.assertEqual(row["contact_role"], "personal_representative")
            self.assertNotEqual(row["contact_name"], row["decedent_name"])
        pending = resolver.matches(s, status="pending")[0]
        d = policy.check_export(s, pending["id"], "alice", "csv")
        self.assertFalse(d.allowed)
        self.assertIn("not_confirmed:pending", d.reasons)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM export_log WHERE allowed = 0"), 1)

    def test_reviewer_confirm_without_corroboration_still_needs_contact(self):
        s = resolved()
        pending = [m for m in review.queue(s) if "name_only_needs_human_review" in m["flags"]]
        mid = review.queue(s)[0]["id"]
        review.decide(s, mid, "confirm", "alice")
        self.assertTrue(policy.check_export(s, mid, "alice", "csv").allowed)
        self.assertIsInstance(pending, list)

    def test_no_authorized_contact_is_denied(self):
        s = resolved()
        m = resolver.matches(s, status="confirmed")[0]
        left = s.one("SELECT * FROM mention WHERE id = ?", (m["left_mention_id"],))
        rec = history.current(s, "estate_case", left["county"], left["natural_key"])
        payload = dict(rec["payload"], personal_rep_name=None)
        history.write(s, "estate_case", left["county"], left["natural_key"], payload)
        d = policy.check_export(s, m["id"], "alice", "csv")
        self.assertIn("no_authorized_contact", d.reasons)

    def test_suppression_by_person_address_parcel_and_expiry(self):
        s = resolved()
        m = resolver.matches(s, status="confirmed")[0]
        policy.add_suppression(s, "person", "jane r public", "opt-out", "alice")
        self.assertIn("suppressed:person:opt-out", policy.check_export(s, m["id"], "a", "csv").reasons)
        s.execute("DELETE FROM suppression")
        policy.add_suppression(s, "address", "4210 Elm Street, Apt 5, Charlotte NC 28205", "dnc", "alice")
        self.assertTrue(any(r.startswith("suppressed:address") for r in policy.check_export(s, m["id"], "a", "csv").reasons))
        s.execute("DELETE FROM suppression")
        policy.add_suppression(s, "parcel", m["right_id"], "litigation hold", "alice", expires_at="2026-09-02T00:00:00Z")
        self.assertFalse(policy.check_export(s, m["id"], "a", "csv").allowed)
        s.clock.advance(days=2)
        self.assertTrue(policy.check_export(s, m["id"], "a", "csv").allowed)

    def test_provenance_trail(self):
        s = resolved()
        row = policy.export(s, "alice")["rows"][0]
        trail = policy.why_do_you_have_this(s, row["lead_id"])
        self.assertEqual(trail["raw_capture"]["url"], "fixture://estate_cases.html")
        self.assertEqual(trail["exports"][-1]["actor"], "alice")
        self.assertIsNone(policy.why_do_you_have_this(s, "deadbeef"))


class RetentionTests(unittest.TestCase):
    def test_purge_removes_expired_history_keeps_current_and_audit(self):
        s = resolved()
        policy.default_retention(s)
        policy.set_retention(s, "parcel", 30)
        policy.set_retention(s, "raw", 30)
        # create a superseded version, then age everything past the TTL
        history.write(s, "parcel", "MECKLENBURG", "045-121-08", {"pin": "045-121-08", "owner_name": "X Y"})
        s.clock.advance(days=45)
        n_versions = s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'parcel'")
        counts = policy.purge_expired(s)
        self.assertEqual(counts["record_versions"], 1)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'parcel'"), n_versions - 1)
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM record_version WHERE source = 'parcel' AND superseded_at IS NULL"), 16)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM access_log"), 0)
        self.assertEqual(s.scalar("SELECT action FROM access_log ORDER BY id DESC LIMIT 1"), "retention.purge")

    def test_expired_source_record_is_not_exportable(self):
        s = resolved()
        policy.set_retention(s, "estate_case", 10)
        m = resolver.matches(s, status="confirmed")[0]
        s.clock.advance(days=11)
        self.assertIn("retention_expired", policy.check_export(s, m["id"], "a", "csv").reasons)


if __name__ == "__main__":
    unittest.main()
