"""Command line: the whole pipeline, one verb at a time.

monitorclt init
monitorclt ingest --county MECKLENBURG --fixtures fixtures/mecklenburg
monitorclt resolve
monitorclt review-queue / decide / import-labels / train / evaluate
monitorclt export / rank / status / watchlist / suppress / purge / serve / contract
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__, counties, evaluate, ingest, policy, quality, review, signals, watch  # noqa: F401  (counties registers plugins)
from .resolve import resolve
from .resolve.model import load_rules
from .sources.base import registry
from .sources.contract import check
from .sources.transport import FixtureTransport, HttpTransport
from .store import Store

DEFAULT_DB = os.environ.get("MONITORCLT_DB", "monitorclt.db")


def _store(args: argparse.Namespace) -> Store:
    store = Store(args.db)
    store.migrate()
    return store


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    elif isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _endpoints(values: Optional[List[str]]) -> Dict[str, str]:
    out = {}
    for v in values or []:
        name, _, url = v.partition("=")
        out[name] = url
    return out


# ---------------------------------------------------------------- commands ---


def cmd_init(args: argparse.Namespace) -> int:
    store = _store(args)
    policy.default_retention(store)
    print("initialized {0} (schema + default retention); counties: {1}".format(args.db, ", ".join(registry.counties())))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    store = _store(args)
    endpoints = _endpoints(args.endpoint)
    transport = FixtureTransport(args.fixtures) if args.fixtures else HttpTransport()
    results = []
    for connector in counties.mecklenburg.connectors(endpoints) if args.county.upper() == counties.mecklenburg.COUNTY else registry.connectors(args.county):
        if args.source and connector.name not in args.source:
            continue
        r = ingest.ingest(store, connector, transport)
        results.append(r)
        print(
            "{0:<18} {1:<7} seen {2:>5}  new {3:>5}  changed {4:>5}  retired {5:>5}  failed {6:>3}  events {7:>4}".format(
                r["connector"], r["status"], r["rows_seen"], r["rows_new"], r["rows_changed"], r["rows_retired"], r["rows_failed"], len(r["events"])
            )
        )
        if r["error"]:
            print("    " + r["error"].strip().replace("\n", "\n    "))
    return 0 if all(r.ok for r in results) else 1


def cmd_resolve(args: argparse.Namespace) -> int:
    store = _store(args)
    r = resolve(store, rules=load_rules(args.rules))
    c = r["counts"]
    print(
        "resolver run {0}: {1} subjects, {2} skipped, {3} blocked out, {4} candidates -> {5} confirmed, {6} pending, {7} rejected ({8} kept reviewer decisions)".format(
            r["run_id"], c["subjects"], c["skipped"], c["blocked_out"], c["candidates"], c["confirmed"], c["pending"], c["rejected"], c["kept_reviewed"]
        )
    )
    for s in r["skipped"]:
        print("  skipped {0}: {1} -- {2}".format(s["left_id"], s["name"], s["reason"]))
    for b in r["blocked_out"]:
        print("  no candidate for {0}".format(b))
    return 0


def cmd_review_queue(args: argparse.Namespace) -> int:
    store = _store(args)
    q = review.queue(store, args.limit)
    if args.json:
        _print(q, True)
        return 0
    for m in q:
        print(
            "#{0:<5} {1:.3f}  {2:<26} {3:<24} {4}  {5}".format(
                m["id"], m["probability"], m["left_id"], m["right_id"], ", ".join(m["evidence"]), ", ".join(m["flags"])
            )
        )
    print("{0} pending".format(len(q)))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    store = _store(args)
    m = review.decide(store, args.match_id, args.decision, args.reviewer, args.note)
    print("match {0} -> {1} (by {2})".format(m["id"], m["status"], m["reviewer"] or "rules"))
    return 0


def cmd_import_labels(args: argparse.Namespace) -> int:
    store = _store(args)
    r = review.import_labels(store, args.path)
    print("imported {0} labels".format(r["added"]))
    for p in r["problems"]:
        print("  " + p)
    return 0 if not r["problems"] else 1


def cmd_train(args: argparse.Namespace) -> int:
    store = _store(args)
    r = review.train(store, load_rules(args.rules))
    if r["version_id"] is None:
        print("no labels with scored candidates; nothing to train on")
        return 1
    m = r["metrics"]
    print(
        "model version {0}: trained on {1} labels ({2} positive), loss {3}, Brier {4}".format(
            r["version_id"], m["n"], m["positives"], m["loss"], m["reliability"]["brier"]
        )
    )
    for k, v in sorted(r["model"]["weights"].items(), key=lambda kv: -abs(kv[1])):
        print("  {0:<28} {1:+.3f}".format(k, v))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    store = _store(args)
    ev = evaluate.evaluate(store, rules=load_rules(args.rules), target_precision=args.target_precision)
    _print(ev if args.json else evaluate.format_report(ev), args.json)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _store(args)
    r = policy.export(store, args.actor, "csv" if args.csv else "cli")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(policy.EXPORT_FIELDS))
            w.writeheader()
            for row in r["rows"]:
                w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in row.items()})
        print("wrote {0} rows to {1}".format(len(r["rows"]), args.csv))
    else:
        _print(r["rows"], True)
    for d in r["denied"]:
        print("denied match {0}: {1}".format(d["match_id"], ", ".join(d["reasons"])), file=sys.stderr)
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = signals.rank(store, args.county, args.limit, args.min_score, args.zip)
    _print(rows if args.json else signals.format_rank(rows), args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = quality.snapshot(store)
    _print(rows if args.json else quality.format_status(rows), args.json)
    return 1 if any(r["anomalies"] for r in rows) and args.strict else 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    store = _store(args)
    if args.action == "create":
        wid = watch.create_watchlist(store, args.name, args.owner, json.loads(args.filters or "{}"), args.channel, args.endpoint, args.secret)
        print("watchlist {0} created".format(wid))
    elif args.action == "list":
        _print(watch.watchlists(store, active_only=False), True)
    elif args.action == "run":
        _print(watch.evaluate_watchlists(store, base_url=args.base_url), True)
    elif args.action == "digest":
        watch.evaluate_watchlists(store, base_url=args.base_url)
        print(watch.digest(store, args.id, mark_delivered=args.deliver))
    elif args.action == "deliver":
        import urllib.request

        def sender(url: str, headers: Dict[str, str], body: bytes) -> int:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - subscriber-provided https endpoint
                return int(resp.status)

        watch.evaluate_watchlists(store, base_url=args.base_url)
        _print(watch.deliver(store, sender), True)
    return 0


def cmd_suppress(args: argparse.Namespace) -> int:
    store = _store(args)
    sid = policy.add_suppression(store, args.kind, args.value, args.reason, args.actor, args.expires)
    print("suppression {0} recorded".format(sid))
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    store = _store(args)
    policy.default_retention(store)
    _print(policy.purge_expired(store), True)
    return 0


def cmd_provenance(args: argparse.Namespace) -> int:
    store = _store(args)
    trail = policy.why_do_you_have_this(store, args.lead_id)
    if trail is None:
        print("no exported lead {0}".format(args.lead_id))
        return 1
    _print(trail, True)
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    ok = True
    for connector in registry.connectors(args.county):
        if args.source and connector.name not in args.source:
            continue
        golden = os.path.join(args.fixtures, "golden", connector.name + ".json") if args.golden else None
        if golden:
            os.makedirs(os.path.dirname(golden), exist_ok=True)
        r = check(connector, args.fixtures, golden, args.write_golden)
        ok = ok and r["ok"]
        print("{0:<18} {1:>4} records  golden={2}  {3}".format(r["connector"], r["records"], r["golden"], "ok" if r["ok"] else "PROBLEMS"))
        for p in r["problems"]:
            print("    " + p)
    return 0 if ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import serve

    store = _store(args)
    serve(store, args.host, args.port, args.token)
    return 0


# ------------------------------------------------------------------ parser ---


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="monitorclt", description=__doc__.strip().splitlines()[0])
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path (default: $MONITORCLT_DB or monitorclt.db)")
    p.add_argument("--version", action="version", version="monitorclt {0}".format(__version__))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the schema and default retention policy").set_defaults(fn=cmd_init)

    s = sub.add_parser("ingest", help="fetch, capture, version and index one county's sources")
    s.add_argument("--county", required=True)
    s.add_argument("--fixtures", help="serve bodies from this directory instead of the network")
    s.add_argument("--source", action="append", help="limit to these sources (repeatable)")
    s.add_argument("--endpoint", action="append", help="override an endpoint: source=url (repeatable)")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("resolve", help="run the resolver over current mentions")
    s.add_argument("--rules")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("review-queue", help="what a human still has to clear")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_review_queue)

    s = sub.add_parser("decide", help="record a reviewer decision (becomes a label)")
    s.add_argument("match_id", type=int)
    s.add_argument("decision", choices=["confirm", "reject", "skip"])
    s.add_argument("--reviewer", required=True)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_decide)

    s = sub.add_parser("import-labels", help="load a label CSV (file_number, pin, is_match[, note])")
    s.add_argument("path")
    s.set_defaults(fn=cmd_import_labels)

    s = sub.add_parser("train", help="fit the link model to labels (shrunk toward the seed rules)")
    s.add_argument("--rules")
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("evaluate", help="precision/recall at both operating points, sweep, calibration")
    s.add_argument("--rules")
    s.add_argument("--target-precision", type=float, default=0.95)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser("export", help="policy-gated lead export")
    s.add_argument("--actor", required=True)
    s.add_argument("--csv", help="write rows to this CSV path")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("rank", help="parcel signal stack, highest first")
    s.add_argument("--county")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--min-score", type=float, default=1.0)
    s.add_argument("--zip", action="append")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_rank)

    s = sub.add_parser("status", help="data freshness, deltas, parse failures, drift")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="exit 1 if any anomaly")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("watchlist", help="create / list / run / digest / deliver")
    s.add_argument("action", choices=["create", "list", "run", "digest", "deliver"])
    s.add_argument("--id", type=int)
    s.add_argument("--name")
    s.add_argument("--owner", default="cli")
    s.add_argument("--filters", help="JSON: counties, zips, kinds, min_assessed_value, land_use, polygon, radius")
    s.add_argument("--channel", default="digest", choices=["digest", "webhook"])
    s.add_argument("--endpoint")
    s.add_argument("--secret")
    s.add_argument("--deliver", action="store_true", help="mark digest notifications delivered")
    s.add_argument("--base-url", default="monitorclt://")
    s.set_defaults(fn=cmd_watchlist)

    s = sub.add_parser("suppress", help="add a person / address / parcel / county to the suppression list")
    s.add_argument("kind", choices=["person", "address", "parcel", "county"])
    s.add_argument("value")
    s.add_argument("--reason", required=True)
    s.add_argument("--actor", default="cli")
    s.add_argument("--expires")
    s.set_defaults(fn=cmd_suppress)

    sub.add_parser("purge", help="apply retention: delete what we should no longer hold").set_defaults(fn=cmd_purge)

    s = sub.add_parser("provenance", help="why do we have this lead? full trail for one lead id")
    s.add_argument("lead_id")
    s.set_defaults(fn=cmd_provenance)

    s = sub.add_parser("contract", help="run connector contract tests against recorded fixtures")
    s.add_argument("--county", required=True)
    s.add_argument("--fixtures", required=True)
    s.add_argument("--source", action="append")
    s.add_argument("--golden", action="store_true", help="compare against fixtures/golden/<source>.json")
    s.add_argument("--write-golden", action="store_true")
    s.set_defaults(fn=cmd_contract)

    s = sub.add_parser("serve", help="HTTP API + reviewer UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
