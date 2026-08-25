#!/usr/bin/env python3
"""Validate the Seller Engine specification.

A spec that is only prose drifts the moment someone edits half of it. These
checks run the executable parts and, more importantly, cross-check the files
against each other — the SQL enums against the state machines, the events
against the transitions, the payloads against the PII rule.

    python3 validate_spec.py            # exit 0 if the spec is coherent
    python3 validate_spec.py --sql      # also execute the DDL (needs postgres)
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PII_TOKENS = {"first_name", "last_name", "phone", "email", "address",
              "street", "full_name", "owner_name"}


def load(module_file):
    spec = importlib.util.spec_from_file_location(module_file.stem.replace("-", "_"),
                                                  module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_enum_values(sql, type_name):
    """Pull the value list out of a CREATE TYPE ... AS ENUM (...) block."""
    match = re.search(rf"CREATE TYPE {re.escape(type_name)} AS ENUM\s*\((.*?)\);",
                      sql, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return set(re.findall(r"'([A-Z_]+)'", match.group(1)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", action="store_true", help="execute the DDL against postgres")
    args = parser.parse_args()

    errors, checks = [], 0

    def check(label, ok, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print(f"  pass  {label}")
        else:
            errors.append(f"{label}{': ' + detail if detail else ''}")

    # ── files present ────────────────────────────────────────────────────────
    expected = ["01-schema.sql", "01a-constraint-tests.sql", "02-events.json",
                "03-api.md", "04-state-machines.py", "05-scoring.py",
                "06-enrichment-and-slas.md", "07-wireframes.md",
                "08-operations.md", "09-dependency-graph.md", "README.md"]
    missing = [f for f in expected if not (HERE / f).exists()]
    check("all spec files present", not missing, ", ".join(missing))

    sql = (HERE / "01-schema.sql").read_text()
    events = json.loads((HERE / "02-events.json").read_text())
    sm = load(HERE / "04-state-machines.py")
    scoring = load(HERE / "05-scoring.py")

    # ── executable specs ─────────────────────────────────────────────────────
    for name, script in (("state machines", "04-state-machines.py"),
                         ("scoring", "05-scoring.py")):
        result = subprocess.run([sys.executable, str(HERE / script)],
                                capture_output=True, text=True)
        check(f"{name} self-tests pass", result.returncode == 0,
              result.stderr.strip()[:200])

    # ── cross-check: SQL enums vs the state machines ────────────────────────
    # This is where specs drift: someone adds a status to one and not the other,
    # and the mismatch only shows up as a runtime enum error in production.
    for type_name, transitions, label in (
        ("seller.inquiry_status", sm.INQUIRY_TRANSITIONS, "inquiry"),
        ("lead.opportunity_status", sm.OPPORTUNITY_TRANSITIONS, "opportunity"),
    ):
        enum_values = sql_enum_values(sql, type_name)
        if enum_values is None:
            check(f"{label}: enum {type_name} found in schema", False)
            continue
        py_states = set(transitions)
        check(f"{label}: SQL enum matches state machine exactly",
              enum_values == py_states,
              f"sql-only={sorted(enum_values - py_states)} "
              f"py-only={sorted(py_states - enum_values)}")

    # ── cross-check: event transitions reference real transitions ───────────
    declared = []
    for event in events["events"]:
        if "transition" not in event:
            continue
        match = re.match(r"([A-Z_]+)\s*->\s*([A-Z_]+)", event["transition"])
        if match:
            declared.append((event["type"], match.group(1), match.group(2)))

    bad = []
    for event_type, src, dst in declared:
        in_inquiry = dst in sm.INQUIRY_TRANSITIONS.get(src, set())
        in_opp = dst in sm.OPPORTUNITY_TRANSITIONS.get(src, set())
        if not (in_inquiry or in_opp):
            bad.append(f"{event_type} claims {src}->{dst}")
    check(f"event transitions match a state machine ({len(declared)} declared)",
          not bad, "; ".join(bad))

    # ── event catalog hygiene ────────────────────────────────────────────────
    no_emitter = [e["type"] for e in events["events"] if not e.get("emitted_by")]
    check("every event declares an emitter", not no_emitter, ", ".join(no_emitter))

    no_consumer = [e["type"] for e in events["events"] if not e.get("consumers")]
    check("every event declares at least one consumer", not no_consumer,
          ", ".join(no_consumer))

    dupes = [t for t in {e["type"] for e in events["events"]}
             if [e["type"] for e in events["events"]].count(t) > 1]
    check("no duplicate event types", not dupes, ", ".join(dupes))

    # ── the PII rule is stated; check the payloads actually honour it ───────
    leaks = []
    for event in events["events"]:
        for field in event.get("payload", {}):
            if field.lower() in PII_TOKENS:
                leaks.append(f"{event['type']}.{field}")
    check("no event payload carries PII", not leaks, ", ".join(leaks))

    # ── scoring invariants ───────────────────────────────────────────────────
    check("master weights sum to 1.0",
          round(sum(scoring.MASTER_WEIGHTS.values()), 6) == 1.0)

    # Every domain a score depends on must appear in the SLA table.
    sla_doc = (HERE / "06-enrichment-and-slas.md").read_text().lower()
    undocumented = sorted({
        domain for deps in scoring.SCORE_DEPENDENCIES.values() for domain in deps
        if f"`{domain}_features`" not in sla_doc
    })
    check("every scored enrichment domain has a documented SLA",
          not undocumented, ", ".join(undocumented))

    # A suppressed score must never be promoted to top priority.
    check("a suppressed score can never be P0",
          scoring.priority(None, 100) != "P0")

    # ── optional: run the DDL for real ──────────────────────────────────────
    if args.sql:
        result = subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-f",
                                 str(HERE / "01-schema.sql")],
                                capture_output=True, text=True)
        check("schema DDL executes", result.returncode == 0,
              result.stderr.strip()[:300])

    print()
    if errors:
        print(f"{len(errors)} of {checks} checks FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"all {checks} spec checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
