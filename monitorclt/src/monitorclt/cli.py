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

from . import (  # noqa: F401  (counties registers plugins)
    __version__,
    counties,
    entities,
    evaluate,
    geocode,
    ingest,
    outcomes,
    persons,
    pipeline,
    policy,
    quality,
    review,
    signals,
    watch,
)
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
    if args.fixtures and args.profile == "live" and not endpoints:
        endpoints = counties.mecklenburg_live.fixture_endpoints()
    transport = FixtureTransport(args.fixtures) if args.fixtures else HttpTransport(cookies=True, min_interval_s=1.0)
    results = []
    for connector in registry.connectors(args.county, args.profile, endpoints):
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
    if args.audit:
        q = review.audit_queue(store, args.limit)
    elif args.double:
        q = review.double_review_queue(store, args.double, args.limit)
    else:
        q = review.queue(store, args.limit, order=args.order, reviewer=args.reviewer)
    if args.json:
        _print(q, True)
        return 0
    for m in q:
        print(
            "#{0:<5} {1:.3f}  {2:<26} {3:<24} {4}  {5}".format(
                m["id"], m["probability"], m["left_id"], m["right_id"], ", ".join(m["evidence"]), ", ".join(m["flags"])
            )
        )
    print("{0} in queue".format(len(q)))
    return 0


def cmd_groups(args: argparse.Namespace) -> int:
    store = _store(args)
    gs = review.groups(store, args.limit)
    if args.json:
        _print(gs, True)
        return 0
    for g in gs:
        subj = g["subject"]
        print(
            "{0}  {1}  (PR: {2})  {3} candidates, {4} pending".format(
                g["left_id"], subj.get("decedent_name"), subj.get("personal_rep_name"), len(g["candidates"]), g["pending"]
            )
        )
        for c in g["candidates"]:
            parcel = c.get("parcel") or {}
            print(
                "    [{0:<9}] {1:.3f} {2:<24} {3:<32} {4}".format(
                    c["status"], c["probability"], c["right_id"], (c.get("owner_name") or "")[:32], parcel.get("situs_norm") or ""
                )
            )
    return 0


def cmd_decide_group(args: argparse.Namespace) -> int:
    store = _store(args)
    r = review.decide_group(store, args.left_id, args.confirm or [], args.reviewer, args.note, not args.keep_others)
    print("{0}: confirmed {1}, rejected {2}".format(r["left_id"], r["confirmed"] or "none", len(r["rejected"])))
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    store = _store(args)
    r = review.agreement_report(store)
    if args.json:
        _print(r, True)
        return 0
    print(
        "double-reviewed matches: {0}; pairwise comparisons: {1}; agreement: {2}".format(
            r["double_reviewed"], r["comparisons"], "n/a" if r["agreement"] is None else "{0:.1%}".format(r["agreement"])
        )
    )
    for pair in r["by_pair"]:
        print("  {0} vs {1}: {2:.1%} over {3}".format(pair["reviewers"][0], pair["reviewers"][1], pair["agreement"], pair["n"]))
    for d in r["disagreements"]:
        print("  disagreement on match {0}: {1}".format(d["match_id"], dict(zip(d["reviewers"], d["decisions"]))))
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    store = _store(args)
    r = outcomes.record_outcome(store, args.outcome, args.by, args.match_id, args.lead_id, args.note)
    print("outcome {0} recorded for lead {1}{2}".format(r["outcome"], r["lead_id"], " -> " + ", ".join(r["effects"]) if r["effects"] else ""))
    return 0


def cmd_outcomes_report(args: argparse.Namespace) -> int:
    store = _store(args)
    rep = outcomes.conversion_report(store)
    _print(rep if args.json else outcomes.format_conversion(rep), args.json)
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    store = _store(args)
    _print(persons.cluster_persons(store), True)
    return 0


def cmd_rebuild_blocks(args: argparse.Namespace) -> int:
    store = _store(args)
    print("rebuilt block keys for {0} mentions".format(entities.rebuild_blocks(store)))
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    store = _store(args)
    n = geocode.warm_from_parcels(store)
    print("warmed geocode cache from {0} parcels".format(n))
    if args.static:
        provider = geocode.StaticGeocoder.from_json(args.static)
        hits = 0
        for row in store.query("SELECT DISTINCT address_norm FROM mention WHERE address_norm IS NOT NULL AND current = 1"):
            if geocode.lookup(store, row["address_norm"], provider):
                hits += 1
        store.commit()
        print("static provider resolved {0} mention addresses".format(hits))
    return 0


def cmd_run_daily(args: argparse.Namespace) -> int:
    store = _store(args)
    policy.default_retention(store)
    transport = FixtureTransport(args.fixtures) if args.fixtures else HttpTransport(cookies=True, min_interval_s=1.0)
    endpoints = counties.mecklenburg_live.fixture_endpoints() if (args.fixtures and args.profile == "live") else None
    report = pipeline.run_daily(
        store, args.county, transport, alert_webhook=args.alert_webhook, base_url=args.base_url, sources=args.source, profile=args.profile, endpoints=endpoints
    )
    print(pipeline.summary(report))
    if args.json:
        _print(report, True)
    return 0 if report["ok"] else 1


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
    endpoints = counties.mecklenburg_live.fixture_endpoints() if args.profile == "live" else None
    for connector in registry.connectors(args.county, args.profile, endpoints):
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
    serve(store, args.host, args.port, args.token, args.trust_proxy_header)
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
    s.add_argument("--profile", default="default", help="county plugin profile: default (synthetic sample), live (real endpoints)")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("resolve", help="run the resolver over current mentions")
    s.add_argument("--rules")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("review-queue", help="what a human still has to clear")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--order", choices=list(review.ORDERS), default="value", help="probability | uncertainty | value (default)")
    s.add_argument("--reviewer", help="hide items this reviewer skipped")
    s.add_argument("--audit", action="store_true", help="sampled auto-confirms awaiting an audit look")
    s.add_argument("--double", metavar="REVIEWER", help="items awaiting a second opinion from REVIEWER")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_review_queue)

    s = sub.add_parser("groups", help="estate-centric view: every candidate per estate")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_groups)

    s = sub.add_parser("decide-group", help="confirm chosen parcels for one estate, reject the other pending ones")
    s.add_argument("left_id")
    s.add_argument("--confirm", action="append", help="right_id to confirm (repeatable; none = reject all pending)")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--note")
    s.add_argument("--keep-others", action="store_true", help="do not reject the unselected pending candidates")
    s.set_defaults(fn=cmd_decide_group)

    s = sub.add_parser("agreement", help="inter-reviewer agreement on double-reviewed matches")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_agreement)

    s = sub.add_parser("outcome", help="record what happened after outreach (declined auto-suppresses)")
    s.add_argument("outcome", choices=list(outcomes.OUTCOMES))
    s.add_argument("--match-id", type=int)
    s.add_argument("--lead-id")
    s.add_argument("--by", required=True)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_outcome)

    s = sub.add_parser("outcomes-report", help="conversion by parcel signal and by match evidence")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_outcomes_report)

    sub.add_parser("cluster", help="attach same-person mentions across sources to confirmed persons").set_defaults(fn=cmd_cluster)
    sub.add_parser("rebuild-blocks", help="recompute blocking keys after a nickname/rules change").set_defaults(fn=cmd_rebuild_blocks)

    s = sub.add_parser("geocode", help="warm the geocode cache from parcels and an optional static table")
    s.add_argument("--static", help="JSON file: {address: [lat, lon]}")
    s.set_defaults(fn=cmd_geocode)

    s = sub.add_parser("run-daily", help="ingest -> resolve -> cluster -> watchlists -> deliver -> status; exit 1 on any problem")
    s.add_argument("--county", required=True)
    s.add_argument("--fixtures")
    s.add_argument("--source", action="append")
    s.add_argument("--alert-webhook")
    s.add_argument("--base-url", default="monitorclt://")
    s.add_argument("--profile", default="default")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_run_daily)

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
    s.add_argument("--profile", default="default")
    s.set_defaults(fn=cmd_contract)

    s = sub.add_parser("serve", help="HTTP API + reviewer UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token")
    s.add_argument("--trust-proxy-header", help="take reviewer identity from this header set by an identity-aware proxy (e.g. x-forwarded-user)")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
