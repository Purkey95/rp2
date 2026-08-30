#!/usr/bin/env python3
"""Transports for the multi-model harness: how a provider is actually called.

Two transports, one contract. A `cli` provider is a subprocess -- `claude -p`,
`gemini -p`, `grok -p`, `pplx search web` -- and authenticates however you already
logged that tool in, so no API key sits in the environment. An `http` provider is a
plain HTTPS POST for the cases a CLI cannot cover. Both return the same normalized
result, so `harness.py` never learns which one it got and a route can be flipped
between them by editing `routes.json`.

Everything provider-specific lives in that config -- argv, endpoint, and the paths
used to dig the answer out of the payload -- for the same reason `crossref.py` keeps
its weights in `match_rules.json`: vendors change their flags and their JSON shapes
far more often than this code should change.

Two habits worth knowing about:

  fallback   if no configured text path resolves, raw stdout becomes the text and
             the result is annotated `text_from_stdout`. An unverified argv then
             degrades to "the CLI's own output" instead of silently returning "".
  redaction  any value pulled from the environment is remembered and scrubbed from
             stored payloads and error strings. A key that reaches the run log is
             a key that has leaked.

Pure stdlib.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess  # nosec B404 -- argv comes from routes.json, never a shell
import time
import urllib.error
import urllib.request

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
CA_BUNDLE_VARS = ("AI_HARNESS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


class HarnessError(Exception):
    """A misconfiguration or a bad input -- never a provider saying something unwelcome."""


# ------------------------------------------------------------- templating ---


def render(template, context):
    """Substitute {placeholders} from `context`; an unknown placeholder is an error.

    Only lowercase identifiers are placeholders, so a prompt may contain a JSON
    example ({"pin": ...}) without being mangled. Failing loudly on an unknown name
    is deliberate: the alternative is a prompt that quietly ships the literal text
    "{decedent_name}" to a vendor.
    """
    text = "\n".join(str(line) for line in template) if isinstance(template, list) else str(template)
    missing = sorted({name for name in PLACEHOLDER.findall(text) if name not in context})
    if missing:
        raise HarnessError(
            "template references {0}, which the task does not provide".format(", ".join(missing))
        )
    return PLACEHOLDER.sub(lambda m: str(context[m.group(1)]), text)


def resolve_env(text, secrets):
    """Expand {env:NAME} and remember the value so it can be redacted later."""

    def replace(match):
        name = match.group(1)
        value = os.environ.get(name)
        if not value:
            raise HarnessError("environment variable {0} is not set".format(name))
        secrets.add(value)
        return value

    return ENV_REF.sub(replace, text)


def redact(text, secrets):
    """Scrub known secret values out of anything about to be stored or printed."""
    if not text:
        return text
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***redacted***")
    return out


# ----------------------------------------------------------- path digging ---


def _tokens(path):
    for part in str(path).split("."):
        name, bracket, rest = part.partition("[")
        if name:
            yield name
        if bracket:
            for index in re.findall(r"(\d+)\]", bracket + rest):
                yield int(index)


def dig(obj, path):
    """Resolve a dotted path with list indexes -- 'choices[0].message.content'."""
    current = obj
    for token in _tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return None
            current = current[token]
    return current


def first_present(obj, paths):
    """First path that resolves to something non-empty. Providers disagree on shape."""
    for path in paths or []:
        value = dig(obj, path)
        if value not in (None, "", [], {}):
            return value
    return None


# --------------------------------------------------------------- parsing ---


def _coerce_text(value):
    """Anthropic-style content blocks, a plain string, or a list of either."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return "" if value is None else str(value)


def _citations(raw, spec):
    """Normalize citations to [{url, title}]. Vendors emit bare URLs or objects."""
    if not spec or raw is None:
        return []
    if isinstance(spec, str):
        spec = {"path": spec}
    items = dig(raw, spec.get("path", ""))
    if not isinstance(items, list):
        return []
    url_key, title_key = spec.get("url", "url"), spec.get("title", "title")
    out = []
    for item in items:
        if isinstance(item, str):
            out.append({"url": item, "title": ""})
        elif isinstance(item, dict):
            out.append(
                {
                    "url": str(item.get(url_key) or ""),
                    "title": str(item.get(title_key) or ""),
                }
            )
    return [row for row in out if row["url"]]


def _parse_structured(payload, fmt, text_paths, notes):
    """Return (raw_object, text) for json / jsonl payloads; (None, None) if unparsable."""
    if fmt == "json":
        try:
            raw = json.loads(payload)
        except ValueError:
            notes.append("payload_not_json")
            return None, None
        return raw, _coerce_text(first_present(raw, text_paths))

    # jsonl / stream-json: the answer is in the last event that carries one.
    raw, text = None, None
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        raw = event
        found = _coerce_text(first_present(event, text_paths))
        if found:
            text = found
    if raw is None:
        notes.append("payload_not_jsonl")
    return raw, text


def parse_payload(provider, payload):
    """Normalize one provider's raw output into text / citations / usage / cost."""
    spec = provider.get("response") or {}
    fmt = spec.get("format", "text")
    text_paths = spec.get("text") or []
    notes = []
    raw, text = (None, None)

    if fmt in ("json", "jsonl"):
        raw, text = _parse_structured(payload, fmt, text_paths, notes)

    if not text:
        # An unverified argv or a changed schema lands here rather than returning "".
        text = payload.strip()
        if fmt in ("json", "jsonl"):
            notes.append("text_from_stdout")

    usage = {}
    for name, path in (spec.get("usage") or {}).items():
        value = dig(raw, path)
        if isinstance(value, (int, float)):
            usage[name] = value

    cost = dig(raw, spec["cost_usd"]) if spec.get("cost_usd") else None
    # A CLI agent can exit 0 having failed -- `claude -p` sets is_error and still
    # returns a well-formed envelope. Exit status alone would call that a success.
    failed = bool(dig(raw, spec["error_flag"])) if spec.get("error_flag") else False
    return {
        "text": text,
        "citations": _citations(raw, spec.get("citations")),
        "usage": usage,
        "cost_usd": cost if isinstance(cost, (int, float)) else None,
        "notes": notes + (["provider_reported_error"] if failed else []),
        "failed": failed,
    }


# -------------------------------------------------------------- transports ---


def run_cli(provider, prompt, timeout, cwd=None):
    """Invoke a provider CLI. Never a shell -- argv is a list, straight to execve.

    These binaries are coding agents, not raw model endpoints: given the chance they
    will read and write files. `cwd` is where you put them (harness.py hands them a
    scratch directory), and the sandbox flags belong in the route's argv.
    """
    secrets = set()
    context = {"prompt": prompt, "model": provider.get("model", "")}
    argv = [resolve_env(render(token, context), secrets) for token in provider["argv"]]
    started = time.time()
    try:
        proc = subprocess.run(  # nosec B603 -- argv list from config, shell=False
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError:
        return _failure(
            "cli not found on PATH: {0} -- install it or route this task to http".format(argv[0]),
            started,
            secrets,
        )
    except subprocess.TimeoutExpired:
        return _failure("cli timed out after {0}s".format(timeout), started, secrets)

    ok = proc.returncode == 0
    return {
        "ok": ok,
        "payload": redact(proc.stdout, secrets),
        "error": None if ok else redact((proc.stderr or "").strip()[:2000], secrets)
        or "exit status {0}".format(proc.returncode),
        "latency_ms": _elapsed(started),
        "argv": [redact(token, secrets) for token in argv],
    }


def _render_body(node, context, secrets):
    if isinstance(node, dict):
        return {key: _render_body(value, context, secrets) for key, value in node.items()}
    if isinstance(node, list):
        return [_render_body(value, context, secrets) for value in node]
    if isinstance(node, str):
        return resolve_env(render(node, context), secrets)
    return node


def _ssl_context():
    """Honor an explicit CA bundle -- corporate and agent proxies both need this."""
    for name in CA_BUNDLE_VARS:
        bundle = os.environ.get(name)
        if bundle and os.path.exists(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def run_http(provider, prompt, timeout):
    """POST to a provider endpoint. urllib picks up HTTPS_PROXY from the environment."""
    secrets = set()
    context = {"prompt": prompt, "model": provider.get("model", "")}
    started = time.time()
    try:
        url = resolve_env(render(provider["url"], context), secrets)
        headers = {
            key: resolve_env(render(value, context), secrets)
            for key, value in (provider.get("headers") or {}).items()
        }
        body = json.dumps(_render_body(provider.get("body") or {}, context, secrets)).encode("utf-8")
    except HarnessError as error:
        return _failure(str(error), started, secrets)

    headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(  # nosec B310 -- https url from routes.json
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            payload = response.read().decode("utf-8", "replace")
        ok, error = True, None
    except urllib.error.HTTPError as http_error:
        payload = http_error.read().decode("utf-8", "replace")
        ok, error = False, "http {0}: {1}".format(http_error.code, payload[:500])
    except Exception as other:  # pylint: disable=broad-except
        payload, ok, error = "", False, "{0}: {1}".format(type(other).__name__, other)

    return {
        "ok": ok,
        "payload": redact(payload, secrets),
        "error": redact(error, secrets),
        "latency_ms": _elapsed(started),
        "argv": ["POST", redact(provider.get("url", ""), secrets)],
    }


def _elapsed(started):
    return int(round((time.time() - started) * 1000))


def _failure(message, started, secrets):
    return {
        "ok": False,
        "payload": "",
        "error": redact(message, secrets),
        "latency_ms": _elapsed(started),
        "argv": [],
    }


TRANSPORTS = {"cli": run_cli, "http": run_http}


def invoke(provider, prompt, timeout, cwd=None):
    """Dispatch on the provider's declared transport and normalize what comes back."""
    transport = provider.get("transport", "cli")
    if transport not in TRANSPORTS:
        raise HarnessError("unknown transport {0!r}".format(transport))
    call = (
        run_cli(provider, prompt, timeout, cwd=cwd)
        if transport == "cli"
        else run_http(provider, prompt, timeout)
    )
    parsed = parse_payload(provider, call["payload"]) if call["payload"] else None
    if parsed and parsed.pop("failed", False):
        call["ok"] = False
        call["error"] = call["error"] or "provider reported an error: {0}".format(parsed["text"][:500])
    result = {
        "ok": call["ok"],
        "transport": transport,
        "payload": call["payload"],
        "error": call["error"],
        "latency_ms": call["latency_ms"],
        "invocation": call["argv"],
        "text": "",
        "citations": [],
        "usage": {},
        "cost_usd": None,
        "notes": [],
    }
    if parsed:
        result.update(parsed)
    return result
