import unittest

from mclt_helpers import fresh_store

from monitorclt import history, raw


class HistoryTests(unittest.TestCase):
    def test_versions_are_append_only_and_bitemporal(self):
        s = fresh_store("2026-09-01T00:00:00Z")
        outcome, v1, d = history.write(s, "parcel", "C", "K", {"owner": "A"}, effective_date="2026-01-01")
        self.assertEqual(outcome, "new")
        self.assertEqual(d["owner"]["to"], "A")
        self.assertEqual(history.write(s, "parcel", "C", "K", {"owner": "A"})[0], "unchanged")
        s.clock.advance(days=1)
        outcome, v2, d = history.write(s, "parcel", "C", "K", {"owner": "B"}, effective_date="2026-09-02")
        self.assertEqual((outcome, d), ("changed", {"owner": {"from": "A", "to": "B"}}))
        versions = history.versions(s, "parcel", "C", "K")
        self.assertEqual([v["version_no"] for v in versions], [1, 2])
        self.assertEqual(versions[0]["superseded_at"], "2026-09-02T00:00:00Z")
        self.assertIsNone(versions[1]["superseded_at"])
        # system time: what we knew on Sept 1
        self.assertEqual(history.as_of(s, "parcel", "C", "K", "2026-09-01T12:00:00Z")["payload"], {"owner": "A"})
        # valid time: what the source said was true on Jun 1
        self.assertEqual(history.effective_as_of(s, "parcel", "C", "K", "2026-06-01")["payload"], {"owner": "A"})
        self.assertEqual(history.effective_as_of(s, "parcel", "C", "K", "2026-12-01")["payload"], {"owner": "B"})
        self.assertEqual(history.current(s, "parcel", "C", "K")["payload"], {"owner": "B"})

    def test_retire_and_change_stream(self):
        s = fresh_store()
        history.write(s, "parcel", "C", "K", {"owner": "A"})
        history.write(s, "parcel", "C", "K", {"owner": "B"})
        self.assertIsNotNone(history.retire(s, "parcel", "C", "K"))
        self.assertIsNone(history.retire(s, "parcel", "C", "K"))
        self.assertEqual(history.current_keys(s, "parcel", "C"), [])
        kinds = [e["kind"] for e in history.events_since(s)]
        self.assertEqual(kinds, ["record_created", "record_changed", "record_retired"])
        changed = history.events_since(s, kinds=["record_changed"])[0]
        self.assertEqual(changed["payload"]["diff"], {"owner": {"from": "A", "to": "B"}})
        # a retired record that reappears is a change, not an unchanged no-op
        self.assertEqual(history.write(s, "parcel", "C", "K", {"owner": "B"})[0], "changed")

    def test_raw_capture_dedupes_by_hash(self):
        s = fresh_store()
        a = raw.capture(s, "parcel", "C", b"hello", "u1")
        b = raw.capture(s, "parcel", "C", b"hello", "u2")
        c = raw.capture(s, "parcel", "C", b"hello!", "u1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(raw.read(s, c), b"hello!")


if __name__ == "__main__":
    unittest.main()
