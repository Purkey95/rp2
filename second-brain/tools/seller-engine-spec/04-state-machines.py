#!/usr/bin/env python3
"""Seller Engine state machines — executable specification.

The blueprint (§9, §10) lists the states. This file defines the legal
TRANSITIONS between them, which is where the actual rules live, and validates the
machines for the failure modes that bite in production: unreachable states,
non-terminal dead ends, and transitions into states nothing can leave.

    python3 04-state-machines.py            # validate both machines
    python3 04-state-machines.py --mermaid  # emit diagrams

Every transition here corresponds to exactly one event in 02-events.json, and
every applied transition writes a row to seller.inquiry_transition or
lead.opportunity_transition. Status is never overwritten without that row.
"""

import argparse
import sys

# ── Seller inquiry (blueprint §9) ────────────────────────────────────────────
# Only QUALIFIED may become an opportunity.

INQUIRY_INITIAL = "STARTED"

INQUIRY_TERMINAL = {
    "QUALIFIED", "ABANDONED", "INVALID", "DUPLICATE", "SPAM", "REJECTED",
}

# MANUAL_REVIEW is deliberately NOT terminal: a human must route it onward.
INQUIRY_TRANSITIONS = {
    "STARTED":                {"PROPERTY_ENTERED", "ABANDONED"},
    "PROPERTY_ENTERED":       {"CONTACT_ENTERED", "ABANDONED", "INVALID"},
    "CONTACT_ENTERED":        {"OTP_PENDING", "ABANDONED"},
    "OTP_PENDING":            {"PHONE_VERIFIED", "ABANDONED", "SPAM"},
    "PHONE_VERIFIED":         {"QUESTIONNAIRE_COMPLETE", "ABANDONED"},
    "QUESTIONNAIRE_COMPLETE": {"SUBMITTED", "ABANDONED"},
    "SUBMITTED":              {"ENRICHING"},
    "ENRICHING":              {"QUALITY_REVIEW", "MANUAL_REVIEW"},
    "QUALITY_REVIEW":         {"QUALIFIED", "REJECTED", "DUPLICATE", "SPAM",
                               "INVALID", "MANUAL_REVIEW"},
    "MANUAL_REVIEW":          {"QUALIFIED", "REJECTED", "DUPLICATE", "INVALID",
                               "SPAM"},
    # terminal
    "QUALIFIED": set(), "ABANDONED": set(), "INVALID": set(),
    "DUPLICATE": set(), "SPAM": set(), "REJECTED": set(),
}

# Guards: a transition may only fire when its precondition holds. These are the
# rules a coding agent would otherwise invent independently and inconsistently.
INQUIRY_GUARDS = {
    ("OTP_PENDING", "PHONE_VERIFIED"):
        "verification_session.status == 'VERIFIED' AND not expired",
    ("QUESTIONNAIRE_COMPLETE", "SUBMITTED"):
        "consent_record exists with onward_disclosure_shown = true  [REVIEW F4]",
    ("SUBMITTED", "ENRICHING"):
        "address_resolution.resolution_status IN ('EXACT','HIGH_CONFIDENCE')",
    ("ENRICHING", "MANUAL_REVIEW"):
        "enrichment_snapshot.is_scoreable = false  [REVIEW F3 stale-data gate]",
    ("ENRICHING", "QUALITY_REVIEW"):
        "enrichment_snapshot.is_scoreable = true",
    ("QUALITY_REVIEW", "QUALIFIED"):
        "no quality_check with status = 'FAIL' AND phone_verified = true",
    ("QUALITY_REVIEW", "DUPLICATE"):
        "duplicate_probability > 0.95",
    ("QUALITY_REVIEW", "MANUAL_REVIEW"):
        "duplicate_probability BETWEEN 0.70 AND 0.95, or any check = 'WARN'",
}

# ── Opportunity (blueprint §10) ──────────────────────────────────────────────

OPPORTUNITY_INITIAL = "QUALIFIED"

OPPORTUNITY_TERMINAL = {
    "CLOSED_WON", "CLOSED_LOST", "DISQUALIFIED", "REFUNDED", "RETURNED", "EXPIRED",
}

OPPORTUNITY_TRANSITIONS = {
    "QUALIFIED":         {"ROUTABLE", "DISQUALIFIED", "EXPIRED"},
    "ROUTABLE":          {"ASSIGNED", "EXPIRED", "DISQUALIFIED"},
    "ASSIGNED":          {"DELIVERED", "RETURNED", "REFUNDED"},
    "DELIVERED":         {"CONTACT_ATTEMPTED", "RETURNED", "REFUNDED", "EXPIRED"},
    "CONTACT_ATTEMPTED": {"CONTACTED", "CLOSED_LOST", "REFUNDED", "EXPIRED"},
    "CONTACTED":         {"APPOINTMENT", "CLOSED_LOST"},
    "APPOINTMENT":       {"OFFER", "CLOSED_LOST"},
    "OFFER":             {"CONTRACT", "CLOSED_LOST"},
    "CONTRACT":          {"CLOSED_WON", "CLOSED_LOST"},
    # terminal
    "CLOSED_WON": set(), "CLOSED_LOST": set(), "DISQUALIFIED": set(),
    "REFUNDED": set(), "RETURNED": set(), "EXPIRED": set(),
}

OPPORTUNITY_GUARDS = {
    ("QUALIFIED", "ROUTABLE"):
        "master_score IS NOT NULL  (a suppressed score blocks routing) [REVIEW F3]",
    ("ROUTABLE", "ASSIGNED"):
        "atomic RESERVE -> CHARGE -> ASSIGN holding the opportunity lock  (§44)",
    ("ASSIGNED", "DELIVERED"):
        "at least one delivery.status = 'CONFIRMED'",
    ("CONTRACT", "CLOSED_WON"):
        "outcome.closing row exists",
    ("CONTACTED", "CLOSED_LOST"):
        "outcome.loss_reason row required — never bare CLOSED_LOST  (§56)",
}


def validate(name, transitions, initial, terminal):
    """Check a machine for the failure modes that actually cause incidents."""
    errors = []
    states = set(transitions)

    # 1. Every target state must be declared.
    for src, targets in transitions.items():
        for dst in targets:
            if dst not in states:
                errors.append(f"{name}: {src} -> {dst!r} targets an undeclared state")

    # 2. Terminal states must have no outbound transitions, and vice versa.
    for state in states:
        outbound = transitions[state]
        if state in terminal and outbound:
            errors.append(f"{name}: terminal state {state} has outbound transitions {outbound}")
        if state not in terminal and not outbound:
            errors.append(f"{name}: {state} is a dead end but is not declared terminal")

    # 3. Every state must be reachable from the initial state. An unreachable
    #    state is either a typo or a rule someone forgot to wire up.
    seen, frontier = {initial}, [initial]
    while frontier:
        for dst in transitions[frontier.pop()]:
            if dst not in seen:
                seen.add(dst)
                frontier.append(dst)
    for orphan in sorted(states - seen):
        errors.append(f"{name}: {orphan} is unreachable from {initial}")

    # 4. Every non-terminal state must be able to reach SOME terminal state,
    #    or leads can get permanently stuck in the pipeline.
    reverse = {s: set() for s in states}
    for src, targets in transitions.items():
        for dst in targets:
            reverse[dst].add(src)
    can_finish, frontier = set(terminal), list(terminal)
    while frontier:
        for src in reverse[frontier.pop()]:
            if src not in can_finish:
                can_finish.add(src)
                frontier.append(src)
    for stuck in sorted(states - can_finish):
        errors.append(f"{name}: {stuck} cannot reach any terminal state")

    return errors


def mermaid(name, transitions, terminal):
    lines = [f"%% {name}", "stateDiagram-v2"]
    for src in transitions:
        for dst in sorted(transitions[src]):
            lines.append(f"    {src} --> {dst}")
    for state in sorted(terminal):
        lines.append(f"    {state} --> [*]")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mermaid", action="store_true")
    args = parser.parse_args()

    machines = [
        ("inquiry", INQUIRY_TRANSITIONS, INQUIRY_INITIAL, INQUIRY_TERMINAL, INQUIRY_GUARDS),
        ("opportunity", OPPORTUNITY_TRANSITIONS, OPPORTUNITY_INITIAL, OPPORTUNITY_TERMINAL, OPPORTUNITY_GUARDS),
    ]

    if args.mermaid:
        for name, trans, _, term, _ in machines:
            print(mermaid(name, trans, term)); print()
        return 0

    errors = []
    for name, trans, init, term, guards in machines:
        found = validate(name, trans, init, term)
        errors += found
        edges = sum(len(t) for t in trans.values())
        status = "FAIL" if found else "ok"
        print(f"  [{status}] {name:12s} {len(trans):>2} states, {edges:>2} transitions, "
              f"{len(guards)} guarded, {len(term)} terminal")
        # A guard must describe a transition that actually exists.
        for (src, dst) in guards:
            if dst not in trans.get(src, set()):
                errors.append(f"{name}: guard {src}->{dst} has no matching transition")

    if errors:
        print("\nFAILURES:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("\nboth state machines valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
