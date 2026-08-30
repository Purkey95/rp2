#!/usr/bin/env python3
"""Multi-model harness: route a task to the model that is actually good at it.

Five vendors, one contract. Which model runs a task is a line in `routes.json`, and
so is how that model gets called -- CLI by default, because `claude`, `gemini`,
`grok` and `pplx` all authenticate from a login you already have and an API key you
do not have to store. The http transport is there for the jobs a CLI cannot do
(schema-enforced JSON, per-call usage accounting) and takes the same route entry.

Three rules the design is built around:

  the matcher stays deterministic
      Nothing here writes a score. `crossref.py` decides matches from evidence and
      `evaluate.py` measures it; a model that could nudge a threshold would make
      both unreproducible. Models run *beside* that pipeline -- fetching a citation,
      reading a scanned deed, helping a human clear the review queue -- and whatever
      they produce enters as evidence with a provenance label, which evaluate.py's
      per-evidence precision table can then hold to account.

  nothing leaves that a task did not declare
      Every task lists `sends`: the exhaustive set of input fields allowed to reach
      that vendor. Anything else in the payload is withheld, and the run log records
      which fields were withheld. `--prompt-only` prints exactly what would be sent
      without sending it.

  disagreement is the product
      A fanout task puts identical evidence to three models and requires a quorum.
      Split verdicts are not averaged into a number -- they escalate to a human.
      That is the only thing three vendors buy that one does not.

Prompts are never written to the run log by default; a sha256 goes in instead, so a
run stays auditable without accumulating a second copy of the records.

    python3 harness.py doctor
    python3 harness.py run --task obituary_confirm --set decedent_name="John Q Public" \\
                           --set county=MECKLENBURG --set date_of_death=2026-01-14
    python3 harness.py run --task review_disambiguation --input-file pending.json

Pure stdlib.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import providers  # noqa: E402  (path must be set first)
from providers import HarnessError  # noqa: E402

TOOL_VERSION = "1.0"
FIXTURES = os.path.join(HERE, "fixtures")
DEFAULT_LOG = os.path.join(HERE, "runs.jsonl")


# ------------------------------------------------------------------ config ---


def load_routes(path=None):
    with open(path or os.path.join(HERE, "routes.json"), encoding="utf-8") as f:
        return json.load(f)


def get_provider(routes, name):
    provider = (routes.get("providers") or {}).get(name)
    if provider is None:
        raise HarnessError("no provider {0!r} in routes.json".format(name))
    merged = dict(routes.get("defaults") or {})
    merged.update(provider)
    return merged


def get_task(routes, name):
    task = (routes.get("tasks") or {}).get(name)
    if task is None:
        known = ", ".join(sorted(routes.get("tasks") or {}))
        raise HarnessError("no task {0!r} in routes.json (have: {1})".format(name, known))
    return task


def task_providers(task):
    return list(task["fanout"]) if task.get("fanout") else [task["provider"]]


# ------------------------------------------------------------ the send gate ---


def build_prompt(task, payload):
    """Render the task prompt from ONLY the fields the task declares in `sends`.

    The allowlist is the whole point: it is a per-vendor data contract you can read
    in one line of config, rather than a promise about what some prompt template
    happens to interpolate today. Fields outside it never enter the context, so a
    template that reaches for one fails loudly instead of leaking it.
    """
    allowed = task.get("sends")
    if allowed is None:
        raise HarnessError("task declares no 'sends' allowlist; refusing to send anything")
    missing = [field for field in allowed if field not in payload]
    if missing:
        raise HarnessError("input is missing required field(s): {0}".format(", ".join(missing)))
    context = {field: payload[field] for field in allowed}
    withheld = sorted(set(payload) - set(allowed))
    return providers.render(task["prompt"], context), withheld


# --------------------------------------------------------------- execution ---


def fixture_path(task_name, provider_name):
    return os.path.join(FIXTURES, "{0}__{1}.json".format(task_name, provider_name))


def replay(provider, task_name, provider_name):
    """Answer from a recorded payload. Tests and demos need neither keys nor CLIs."""
    path = fixture_path(task_name, provider_name)
    if not os.path.exists(path):
        return {
            "ok": False,
            "transport": provider.get("transport", "cli"),
            "payload": "",
            "error": "no fixture at {0} (record one with --record)".format(
                os.path.relpath(path, HERE)
            ),
            "latency_ms": 0,
            "invocation": [],
            "text": "",
            "citations": [],
            "usage": {},
            "cost_usd": None,
            "notes": ["dry_run"],
        }
    with open(path, encoding="utf-8") as f:
        fixture = json.load(f)
    result = {
        "ok": bool(fixture.get("ok", True)),
        "transport": provider.get("transport", "cli"),
        "payload": fixture.get("payload", ""),
        "error": fixture.get("error"),
        "latency_ms": 0,
        "invocation": [],
    }
    result.update(providers.parse_payload(provider, result["payload"]))
    result["notes"] = list(result.get("notes") or []) + ["dry_run"]
    return result


def record(task_name, provider_name, result):
    if not os.path.isdir(FIXTURES):
        os.makedirs(FIXTURES)
    with open(fixture_path(task_name, provider_name), "w", encoding="utf-8") as f:
        json.dump(
            {"ok": result["ok"], "payload": result["payload"], "error": result["error"]},
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")


def call_provider(routes, task_name, task, provider_name, prompt, options):
    """One provider, one prompt. Live or replayed, the result has the same shape."""
    provider = get_provider(routes, provider_name)
    timeout = options.get("timeout") or provider.get("timeout_seconds", 180)
    if options.get("dry_run"):
        result = replay(provider, task_name, provider_name)
    elif provider.get("transport", "cli") == "cli":
        # Coding agents write files if left somewhere writable. Give them nowhere.
        with tempfile.TemporaryDirectory(prefix="ai-harness-") as sandbox:
            result = providers.invoke(provider, prompt, timeout, cwd=options.get("cwd") or sandbox)
    else:
        result = providers.invoke(provider, prompt, timeout)

    result["provider"] = provider_name
    result["model"] = provider.get("model")
    result["task"] = task_name
    if options.get("record") and result["ok"] and not options.get("dry_run"):
        record(task_name, provider_name, result)
    return result


def extract_decision(text, pattern):
    """Pull the verdict token out of prose. The LAST one wins.

    Models restate the question before answering it ("...whether this is a MATCH..."),
    so the first token in the text is routinely the wrong one.
    """
    if not pattern:
        return None
    matches = list(re.finditer(pattern, text or "", re.IGNORECASE))
    if not matches:
        return None
    last = matches[-1]
    return (last.group(1) if last.groups() else last.group(0)).upper()


def tally_quorum(decisions, quorum):
    """Decide only on a clear plurality that clears quorum. Ties escalate."""
    counted = Counter(value for value in decisions if value)
    if not counted:
        return {"decision": None, "votes": {}, "agreement": 0, "escalate": True}
    top_count = counted.most_common(1)[0][1]
    leaders = sorted(value for value, count in counted.items() if count == top_count)
    decided = len(leaders) == 1 and top_count >= quorum
    return {
        "decision": leaders[0] if decided else None,
        "votes": dict(counted),
        "agreement": top_count,
        "escalate": not decided,
    }


def run_task(routes, task_name, payload, options):
    """Execute a task -- single provider or fanout -- and return one result envelope."""
    task = get_task(routes, task_name)
    prompt, withheld = build_prompt(task, payload)
    names = task_providers(task)

    envelope = {
        "tool_version": TOOL_VERSION,
        "routes_version": routes.get("version"),
        "task": task_name,
        "mode": "fanout" if task.get("fanout") else "single",
        "dry_run": bool(options.get("dry_run")),
        "sends": list(task.get("sends") or []),
        "withheld_fields": withheld,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "responses": [],
    }
    if options.get("log_prompts"):
        envelope["prompt"] = prompt

    for provider_name in names:
        envelope["responses"].append(
            call_provider(routes, task_name, task, provider_name, prompt, options)
        )

    envelope["ok"] = any(response["ok"] for response in envelope["responses"])
    envelope["cost_usd"] = _total_cost(envelope["responses"])
    if task.get("fanout"):
        pattern = task.get("decision_pattern")
        decisions = [extract_decision(r["text"], pattern) if r["ok"] else None
                     for r in envelope["responses"]]
        for response, decision in zip(envelope["responses"], decisions):
            response["decision"] = decision
        envelope["quorum"] = task.get("quorum", len(names))
        envelope.update(tally_quorum(decisions, envelope["quorum"]))
    return envelope


def _total_cost(responses):
    costs = [r["cost_usd"] for r in responses if isinstance(r.get("cost_usd"), (int, float))]
    return round(sum(costs), 6) if costs else None


# ----------------------------------------------------------------- run log ---


def log_entry(envelope):
    """What the run log keeps. Not the prompt -- a hash of it, unless asked."""
    entry = {
        key: envelope[key]
        for key in (
            "started_at",
            "task",
            "mode",
            "dry_run",
            "ok",
            "prompt_sha256",
            "prompt_chars",
            "withheld_fields",
            "cost_usd",
        )
    }
    if "prompt" in envelope:
        entry["prompt"] = envelope["prompt"]
    for key in ("decision", "votes", "agreement", "escalate"):
        if key in envelope:
            entry[key] = envelope[key]
    entry["responses"] = [
        {
            "provider": r["provider"],
            "transport": r["transport"],
            "model": r.get("model"),
            "ok": r["ok"],
            "latency_ms": r["latency_ms"],
            "usage": r["usage"],
            "cost_usd": r["cost_usd"],
            "decision": r.get("decision"),
            "citations": len(r["citations"]),
            "text_sha256": hashlib.sha256((r["text"] or "").encode("utf-8")).hexdigest(),
            "notes": r["notes"],
            "error": r["error"],
        }
        for r in envelope["responses"]
    ]
    return entry


def append_log(path, entry):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


# ------------------------------------------------------------------ doctor ---


def diagnose(routes):
    """What is actually callable on this machine, per provider."""
    rows = []
    for name in sorted(routes.get("providers") or {}):
        provider = get_provider(routes, name)
        auth = provider.get("auth") or {}
        env_var = auth.get("env")
        row = {
            "provider": name,
            "transport": provider.get("transport", "cli"),
            "auth": auth.get("kind", "unknown"),
            "env_var": env_var,
            "verified": bool(provider.get("verified")),
            "note": auth.get("note", ""),
        }
        if row["transport"] == "cli":
            binary = provider["argv"][0]
            row["binary"] = binary
            row["path"] = shutil.which(binary)
            row["ready"] = bool(row["path"]) and (not env_var or bool(os.environ.get(env_var)))
        else:
            row["binary"] = None
            row["path"] = None
            row["ready"] = bool(os.environ.get(env_var)) if env_var else True
        row["key_set"] = bool(os.environ.get(env_var)) if env_var else None
        rows.append(row)
    return rows


def format_doctor(rows):
    lines = [
        "multi-model harness -- what this machine can actually call",
        "",
        "  provider           transport  auth          installed  key      argv",
    ]
    for row in rows:
        if row["transport"] == "cli":
            installed = "yes" if row["path"] else "NO"
        else:
            installed = "n/a"
        if row["key_set"] is None:
            key = "n/a"
        else:
            key = "set" if row["key_set"] else "MISSING"
        lines.append(
            "  {0:<18} {1:<10} {2:<13} {3:<10} {4:<8} {5}".format(
                row["provider"],
                row["transport"],
                row["auth"],
                installed,
                key,
                "verified" if row["verified"] else "unverified",
            )
        )
    lines.append("")
    for row in rows:
        if row["note"]:
            lines.append("  {0}: {1}".format(row["provider"], row["note"]))
    lines.append("")
    lines.append(
        "'unverified' means the argv came from vendor docs and has not been run against"
    )
    lines.append(
        "the installed CLI here. Confirm one, then set \"verified\": true in routes.json."
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ report ---


def format_report(envelope):
    lines = [
        "task {0} [{1}]{2}".format(
            envelope["task"], envelope["mode"], "  (dry run)" if envelope["dry_run"] else ""
        ),
        "  sends {0}".format(", ".join(envelope["sends"]) or "nothing"),
    ]
    if envelope["withheld_fields"]:
        lines.append("  withheld {0}".format(", ".join(envelope["withheld_fields"])))
    lines.append("")

    for response in envelope["responses"]:
        head = "  {0} [{1}] {2}ms".format(
            response["provider"], response["transport"], response["latency_ms"]
        )
        if response.get("decision"):
            head += "  -> {0}".format(response["decision"])
        if not response["ok"]:
            head += "  FAILED"
        lines.append(head)
        if response["error"]:
            lines.append("    error: {0}".format(response["error"]))
        if response["text"]:
            for line in response["text"].strip().splitlines():
                lines.append("    {0}".format(line).rstrip())
        for citation in response["citations"]:
            lines.append("    * {0} {1}".format(citation["url"], citation["title"]).rstrip())
        if response["notes"]:
            lines.append("    notes: {0}".format(", ".join(response["notes"])))
        lines.append("")

    if envelope["mode"] == "fanout":
        votes = ", ".join(
            "{0} x{1}".format(name, count) for name, count in sorted(envelope["votes"].items())
        )
        lines.append("QUORUM {0} of {1}".format(envelope["agreement"], envelope["quorum"]))
        lines.append("  votes: {0}".format(votes or "none"))
        if envelope["escalate"]:
            lines.append("  ESCALATE -- no quorum. A human decides this one.")
        else:
            lines.append("  decision: {0}".format(envelope["decision"]))
        lines.append("")
    if envelope["cost_usd"] is not None:
        lines.append("cost ${0:.4f} (providers that report it)".format(envelope["cost_usd"]))
    return "\n".join(lines).rstrip() + "\n"


# -------------------------------------------------------------------- main ---


def collect_payload(args):
    payload = {}
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            payload.update(json.load(f))
    if args.input:
        payload.update(json.loads(args.input))
    for pair in args.set or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise HarnessError("--set expects key=value, got {0!r}".format(pair))
        payload[key.strip()] = value
    return payload


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--routes", help="path to routes.json")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run a task")
    run.add_argument("--task", required=True)
    run.add_argument("--input", help="payload as a JSON object")
    run.add_argument("--input-file", help="payload as a JSON file")
    run.add_argument("--set", action="append", metavar="KEY=VALUE")
    run.add_argument(
        "--prompt-only",
        action="store_true",
        help="print exactly what would be sent, and send nothing",
    )
    run.add_argument("--dry-run", action="store_true", help="replay fixtures; no keys, no CLIs")
    run.add_argument("--record", action="store_true", help="save live responses as fixtures")
    run.add_argument("--timeout", type=int)
    run.add_argument("--cwd", help="working directory for CLI providers (default: a temp dir)")
    run.add_argument("--json", dest="json_out", help="write the full result envelope here")
    run.add_argument("--log", default=DEFAULT_LOG, help="run log path (JSONL)")
    run.add_argument("--no-log", action="store_true")
    run.add_argument(
        "--log-prompts",
        action="store_true",
        help="store rendered prompts in the run log (off by default: they carry the records)",
    )
    run.add_argument("--quiet", action="store_true")

    sub.add_parser("doctor", help="what is installed and authenticated here")
    sub.add_parser("routes", help="list tasks and where they go")
    return parser


def format_routes(routes):
    lines = ["tasks", ""]
    for name in sorted(routes.get("tasks") or {}):
        task = routes["tasks"][name]
        target = (
            "fanout -> {0} (quorum {1})".format(
                ", ".join(task["fanout"]), task.get("quorum", len(task["fanout"]))
            )
            if task.get("fanout")
            else task["provider"]
        )
        lines.append("  {0:<24} {1}".format(name, target))
        lines.append("  {0:<24} sends: {1}".format("", ", ".join(task.get("sends") or [])))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    routes = load_routes(args.routes)

    if args.command == "doctor":
        print(format_doctor(diagnose(routes)))
        return 0
    if args.command == "routes":
        print(format_routes(routes))
        return 0
    if args.command != "run":
        parser.print_help()
        return 2

    payload = collect_payload(args)
    if args.prompt_only:
        task = get_task(routes, args.task)
        prompt, withheld = build_prompt(task, payload)
        print("would send to: {0}".format(", ".join(task_providers(task))))
        if withheld:
            print("withheld: {0}".format(", ".join(withheld)))
        print("-" * 72)
        print(prompt)
        return 0

    envelope = run_task(
        routes,
        args.task,
        payload,
        {
            "dry_run": args.dry_run,
            "record": args.record,
            "timeout": args.timeout,
            "cwd": args.cwd,
            "log_prompts": args.log_prompts,
        },
    )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, sort_keys=True)
    if not args.no_log:
        append_log(args.log, log_entry(envelope))
    if not args.quiet:
        print(format_report(envelope), end="")
    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HarnessError as error:
        sys.stderr.write("error: {0}\n".format(error))
        sys.exit(2)
