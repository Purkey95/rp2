#!/usr/bin/env python3
"""Tests for the multi-model harness.

No network, no API keys, and no vendor CLIs: the CLI transport is exercised against
`sys.executable`, which is a real subprocess with real argv and a real exit status,
and everything else replays the shipped fixtures. That is deliberate -- a test suite
that needs five logins is a test suite nobody runs.

Run directly (`python3 test_harness.py`) or under pytest from the repo root.
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import harness  # noqa: E402  (path must be set first)
import providers  # noqa: E402
from providers import HarnessError  # noqa: E402

ECHO_ARGV = [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", "{prompt}"]


def say(script):
    """A provider that prints `script` -- a stand-in for a vendor CLI."""
    return {
        "transport": "cli",
        "argv": [sys.executable, "-c", "import sys; sys.stdout.write({0!r})".format(script)],
        "response": {"format": "text"},
    }


def routes_with(providers_map, task):
    return {"version": "test", "providers": providers_map, "tasks": {"t": task}}


class TestPathDigging(unittest.TestCase):
    def setUp(self):
        self.obj = {"choices": [{"message": {"content": "hi"}}], "nested": [[0, {"b": 2}]]}

    def test_dotted_path_with_index(self):
        self.assertEqual(providers.dig(self.obj, "choices[0].message.content"), "hi")

    def test_repeated_index(self):
        self.assertEqual(providers.dig(self.obj, "nested[0][1].b"), 2)

    def test_missing_path_is_none_not_an_exception(self):
        self.assertIsNone(providers.dig(self.obj, "choices[9].message.content"))
        self.assertIsNone(providers.dig(self.obj, "nope.at.all"))

    def test_first_present_skips_empty_values(self):
        obj = {"a": "", "b": [], "c": "found"}
        self.assertEqual(providers.first_present(obj, ["a", "b", "c"]), "found")


class TestTemplating(unittest.TestCase):
    def test_lists_join_as_lines(self):
        self.assertEqual(providers.render(["one", "two"], {}), "one\ntwo")

    def test_json_braces_survive(self):
        self.assertEqual(providers.render('{"pin": 1}', {}), '{"pin": 1}')

    def test_unknown_placeholder_is_an_error_not_a_literal(self):
        with self.assertRaises(HarnessError):
            providers.render("hello {decedent_name}", {})

    def test_secrets_are_scrubbed(self):
        self.assertEqual(providers.redact("token=abc123 here", {"abc123"}), "token=***redacted*** here")


class TestSendGate(unittest.TestCase):
    """The allowlist is the data contract; these are the tests that keep it honest."""

    task = {"sends": ["name"], "prompt": "look up {name}"}

    def test_only_declared_fields_reach_the_prompt(self):
        prompt, withheld = harness.build_prompt(
            self.task, {"name": "John Q Public", "ssn": "123-45-6789"}
        )
        self.assertEqual(prompt, "look up John Q Public")
        self.assertNotIn("123-45-6789", prompt)
        self.assertEqual(withheld, ["ssn"])

    def test_template_reaching_past_the_allowlist_fails_loudly(self):
        task = {"sends": ["name"], "prompt": "{name} at {home_address}"}
        with self.assertRaises(HarnessError):
            harness.build_prompt(task, {"name": "X", "home_address": "4210 Elm St"})

    def test_missing_declared_field_is_an_error(self):
        with self.assertRaises(HarnessError):
            harness.build_prompt(self.task, {})

    def test_a_task_without_an_allowlist_sends_nothing(self):
        with self.assertRaises(HarnessError):
            harness.build_prompt({"prompt": "hi"}, {"name": "X"})


class TestCliTransport(unittest.TestCase):
    def test_prompt_reaches_the_subprocess_argv(self):
        provider = {"transport": "cli", "argv": ECHO_ARGV, "response": {"format": "text"}}
        result = providers.invoke(provider, "hello world", timeout=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "hello world")
        self.assertEqual(result["transport"], "cli")

    def test_missing_binary_reports_which_one(self):
        provider = {"transport": "cli", "argv": ["definitely-not-installed-xyz", "{prompt}"]}
        result = providers.invoke(provider, "x", timeout=30)
        self.assertFalse(result["ok"])
        self.assertIn("definitely-not-installed-xyz", result["error"])

    def test_nonzero_exit_is_a_failure(self):
        provider = {
            "transport": "cli",
            "argv": [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        }
        result = providers.invoke(provider, "x", timeout=30)
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])

    def test_secret_in_argv_is_not_echoed_back_into_the_result(self):
        os.environ["AI_HARNESS_TEST_KEY"] = "sk-supersecret"
        try:
            provider = {
                "transport": "cli",
                "argv": ECHO_ARGV[:3] + ["{env:AI_HARNESS_TEST_KEY}"],
                "response": {"format": "text"},
            }
            result = providers.invoke(provider, "x", timeout=30)
            self.assertNotIn("sk-supersecret", json.dumps(result))
            self.assertNotIn("sk-supersecret", " ".join(result["invocation"]))
        finally:
            del os.environ["AI_HARNESS_TEST_KEY"]

    def test_provider_reported_error_beats_a_zero_exit_status(self):
        script = "import sys; sys.stdout.write('{\"is_error\": true, \"result\": \"rate limited\"}')"
        provider = {
            "transport": "cli",
            "argv": [sys.executable, "-c", script],
            "response": {"format": "json", "text": ["result"], "error_flag": "is_error"},
        }
        result = providers.invoke(provider, "x", timeout=30)
        self.assertFalse(result["ok"])
        self.assertIn("rate limited", result["error"])
        self.assertIn("provider_reported_error", result["notes"])

    def test_unset_env_var_fails_before_the_call(self):
        provider = {"transport": "cli", "argv": ["echo", "{env:AI_HARNESS_ABSENT_VAR}"]}
        with self.assertRaises(HarnessError):
            providers.invoke(provider, "x", timeout=30)


class TestParsing(unittest.TestCase):
    def test_unparsable_json_falls_back_to_stdout(self):
        provider = {"response": {"format": "json", "text": ["result"]}}
        parsed = providers.parse_payload(provider, "plain prose, not JSON")
        self.assertEqual(parsed["text"], "plain prose, not JSON")
        self.assertIn("payload_not_json", parsed["notes"])
        self.assertIn("text_from_stdout", parsed["notes"])

    def test_content_blocks_are_concatenated(self):
        provider = {"response": {"format": "json", "text": ["content"]}}
        payload = json.dumps({"content": [{"type": "text", "text": "a"}, {"text": "b"}]})
        self.assertEqual(providers.parse_payload(provider, payload)["text"], "ab")

    def test_jsonl_takes_the_last_event_carrying_text(self):
        provider = {"response": {"format": "jsonl", "text": ["delta"]}}
        payload = '{"delta": "first"}\n{"noise": 1}\n{"delta": "last"}\n'
        self.assertEqual(providers.parse_payload(provider, payload)["text"], "last")

    def test_citations_accept_bare_urls_and_objects(self):
        provider = {"response": {"format": "json", "text": ["t"], "citations": "citations"}}
        payload = json.dumps({"t": "x", "citations": ["https://a.example", {"url": "https://b.example", "title": "B"}]})
        self.assertEqual(
            providers.parse_payload(provider, payload)["citations"],
            [{"url": "https://a.example", "title": ""}, {"url": "https://b.example", "title": "B"}],
        )

    def test_usage_and_cost_are_pulled_by_path(self):
        provider = {
            "response": {
                "format": "json",
                "text": ["result"],
                "usage": {"input_tokens": "usage.input_tokens"},
                "cost_usd": "total_cost_usd",
            }
        }
        payload = json.dumps({"result": "x", "usage": {"input_tokens": 12}, "total_cost_usd": 0.5})
        parsed = providers.parse_payload(provider, payload)
        self.assertEqual(parsed["usage"], {"input_tokens": 12})
        self.assertEqual(parsed["cost_usd"], 0.5)


class TestQuorum(unittest.TestCase):
    pattern = r"\b(NO_MATCH|MATCH|UNSURE)\b"

    def test_last_verdict_wins_over_a_restated_question(self):
        text = "Deciding whether this is a MATCH or not.\n\nNO_MATCH"
        self.assertEqual(harness.extract_decision(text, self.pattern), "NO_MATCH")

    def test_no_match_is_not_read_as_match(self):
        self.assertEqual(harness.extract_decision("NO_MATCH", self.pattern), "NO_MATCH")

    def test_absent_verdict_is_none(self):
        self.assertIsNone(harness.extract_decision("I would rather not say.", self.pattern))

    def test_quorum_reached(self):
        outcome = harness.tally_quorum(["MATCH", "MATCH", "UNSURE"], 2)
        self.assertEqual(outcome["decision"], "MATCH")
        self.assertFalse(outcome["escalate"])

    def test_a_tie_escalates_rather_than_picking(self):
        outcome = harness.tally_quorum(["MATCH", "NO_MATCH"], 2)
        self.assertIsNone(outcome["decision"])
        self.assertTrue(outcome["escalate"])

    def test_three_way_split_escalates(self):
        outcome = harness.tally_quorum(["MATCH", "NO_MATCH", "UNSURE"], 2)
        self.assertTrue(outcome["escalate"])

    def test_all_providers_silent_escalates(self):
        self.assertTrue(harness.tally_quorum([None, None], 2)["escalate"])


class TestRunTask(unittest.TestCase):
    def test_fanout_reaches_a_decision(self):
        routes = routes_with(
            {"a": say("MATCH"), "b": say("reasoning\nMATCH"), "c": say("UNSURE")},
            {
                "fanout": ["a", "b", "c"],
                "quorum": 2,
                "decision_pattern": r"\b(NO_MATCH|MATCH|UNSURE)\b",
                "sends": ["name"],
                "prompt": "is {name} the owner?",
            },
        )
        envelope = harness.run_task(routes, "t", {"name": "John Q Public"}, {})
        self.assertEqual(envelope["decision"], "MATCH")
        self.assertEqual(envelope["agreement"], 2)
        self.assertFalse(envelope["escalate"])
        self.assertEqual([r["decision"] for r in envelope["responses"]], ["MATCH", "MATCH", "UNSURE"])

    def test_a_failed_provider_does_not_vote(self):
        routes = routes_with(
            {"a": say("MATCH"), "b": {"transport": "cli", "argv": ["not-a-real-binary-xyz"]}},
            {
                "fanout": ["a", "b"],
                "quorum": 2,
                "decision_pattern": r"\b(NO_MATCH|MATCH|UNSURE)\b",
                "sends": ["name"],
                "prompt": "{name}",
            },
        )
        envelope = harness.run_task(routes, "t", {"name": "X"}, {})
        self.assertTrue(envelope["escalate"])
        self.assertEqual(envelope["votes"], {"MATCH": 1})

    def test_envelope_is_json_serializable(self):
        routes = routes_with({"a": say("ok")}, {"provider": "a", "sends": [], "prompt": "hi"})
        envelope = harness.run_task(routes, "t", {}, {})
        self.assertTrue(json.dumps(envelope, sort_keys=True))

    def test_unknown_task_names_the_ones_that_exist(self):
        with self.assertRaises(HarnessError) as caught:
            harness.run_task(routes_with({}, {"provider": "a"}), "nope", {}, {})
        self.assertIn("t", str(caught.exception))


class TestFixtureReplay(unittest.TestCase):
    """The shipped routes.json and fixtures, exercised without a single vendor call."""

    def setUp(self):
        self.routes = harness.load_routes()

    def test_shipped_routes_are_coherent(self):
        for name, task in self.routes["tasks"].items():
            self.assertIn("sends", task, name)
            self.assertIn("prompt", task, name)
            for provider_name in harness.task_providers(task):
                self.assertIn(provider_name, self.routes["providers"], name)

    def test_every_provider_declares_a_transport_the_harness_knows(self):
        for name in self.routes["providers"]:
            provider = harness.get_provider(self.routes, name)
            self.assertIn(provider.get("transport", "cli"), providers.TRANSPORTS, name)
            if provider.get("transport", "cli") == "cli":
                self.assertTrue(provider.get("argv"), name)
            else:
                self.assertTrue(provider.get("url"), name)

    def test_obituary_replay_yields_citations(self):
        envelope = harness.run_task(
            self.routes,
            "obituary_confirm",
            {
                "decedent_name": "John Q Public",
                "county": "MECKLENBURG",
                "date_of_death": "2026-01-14",
                "ssn": "123-45-6789",
            },
            {"dry_run": True},
        )
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["withheld_fields"], ["ssn"])
        response = envelope["responses"][0]
        self.assertTrue(response["citations"])
        self.assertTrue(all(c["url"].startswith("http") for c in response["citations"]))

    def test_review_replay_reaches_quorum(self):
        envelope = harness.run_task(
            self.routes,
            "review_disambiguation",
            {
                "decedent_name": "John Q Public",
                "county": "MECKLENBURG",
                "owner_string": "ESTATE OF PUBLIC JOHN Q",
                "situs_address": "4210 ELM ST",
                "score": 0.88,
                "evidence": "estate_marker_on_owner, mailing_address_match",
            },
            {"dry_run": True},
        )
        self.assertEqual(envelope["decision"], "MATCH")
        self.assertEqual(envelope["votes"], {"MATCH": 2, "UNSURE": 1})

    def test_a_missing_fixture_fails_instead_of_calling_a_vendor(self):
        envelope = harness.run_task(
            self.routes, "page_fetch", {"url": "https://example.com"}, {"dry_run": True}
        )
        self.assertFalse(envelope["ok"])
        self.assertIn("no fixture", envelope["responses"][0]["error"])


class TestRunLog(unittest.TestCase):
    def envelope(self, options=None):
        routes = routes_with(
            {"a": say("MATCH")},
            {"provider": "a", "sends": ["name"], "prompt": "look up {name}"},
        )
        return harness.run_task(routes, "t", {"name": "John Q Public"}, options or {})

    def test_prompt_is_hashed_not_stored(self):
        entry = harness.log_entry(self.envelope())
        blob = json.dumps(entry)
        self.assertNotIn("John Q Public", blob)
        self.assertEqual(len(entry["prompt_sha256"]), 64)
        self.assertNotIn("prompt", entry)

    def test_log_prompts_is_opt_in(self):
        entry = harness.log_entry(self.envelope({"log_prompts": True}))
        self.assertIn("John Q Public", entry["prompt"])

    def test_response_text_is_hashed_and_appended_as_jsonl(self):
        entry = harness.log_entry(self.envelope())
        self.assertEqual(len(entry["responses"][0]["text_sha256"]), 64)
        with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as handle:
            path = handle.name
        try:
            harness.append_log(path, entry)
            harness.append_log(path, entry)
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(rows), 2)
        finally:
            os.unlink(path)


class TestDoctor(unittest.TestCase):
    def test_reports_every_provider_with_a_readiness_verdict(self):
        rows = harness.diagnose(harness.load_routes())
        self.assertEqual(len(rows), len(harness.load_routes()["providers"]))
        for row in rows:
            self.assertIn("ready", row)
        self.assertIn("provider", harness.format_doctor(rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
