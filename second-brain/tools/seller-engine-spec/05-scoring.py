#!/usr/bin/env python3
"""Scoring formulas v1 — executable specification.

Blueprint §31-§37. Four scores, all deterministic and rules-based: we do not
launch with "AI says 91". Every score returns its drivers, so the number is
always explainable to an operator, a buyer, or an auditor.

    python3 05-scoring.py           # run the test suite
    python3 05-scoring.py --demo    # score three worked examples

MASTER_SCORE_V1 weights are frozen at G0. Changing them creates MASTER_SCORE_V2;
historical scores keep their version tag so past analysis stays reproducible.

[REVIEW F3] The stale-enrichment gate lives here. A score is not emitted when a
domain it depends on is STALE or MISSING — because on 2026-08-24 four MonitorCLT
pipelines had been dark for 10-12 days while still serving their last-known
values. A stale feed does not announce itself; it just keeps answering.
"""

import argparse
import sys

SCORE_VERSION = "V1"
MASTER_WEIGHTS = {"motivation": 0.40, "property": 0.30, "trust": 0.20, "market": 0.10}

# Which enrichment domains each score depends on. Used by the staleness gate.
SCORE_DEPENDENCIES = {
    "TRUST":      [],                                   # self-reported + verification only
    "MOTIVATION": [],                                   # questionnaire only
    "PROPERTY":   ["property", "financial", "distress"],
    "MASTER":     ["property", "financial", "distress", "market"],
}

FRESH_ENOUGH = {"FRESH", "AGING"}   # STALE and MISSING block scoring


# ── Trust: is this a real seller inquiry? (blueprint §31) ────────────────────

def trust_score(signals):
    """0-100. Starts at a neutral 50 and moves on hard evidence only."""
    drivers, total = [], 50

    def add(points, label):
        nonlocal total
        total += points
        drivers.append({"factor": label, "points": points})

    if signals.get("phone_verified"):        add(+25, "phone_verified")
    else:                                    add(-40, "phone_not_verified")
    if signals.get("address_resolved_exact"): add(+10, "address_resolved_exact")
    if signals.get("address_ambiguous"):     add(-15, "address_ambiguous")
    if signals.get("owner_name_matches"):    add(+15, "owner_name_matches_record")
    if signals.get("owner_name_mismatch"):   add(-20, "owner_name_mismatch")

    dup = signals.get("duplicate_probability", 0.0)
    if dup > 0.95:                           add(-50, "near_certain_duplicate")
    elif dup > 0.70:                         add(-20, "possible_duplicate")

    fraud = signals.get("fraud_probability", 0.0)
    if fraud > 0.70:                         add(-45, "high_fraud_signal")
    elif fraud > 0.40:                       add(-20, "elevated_fraud_signal")

    if signals.get("otp_failures", 0) >= 3:  add(-15, "repeated_otp_failures")

    return _clamp(total), drivers


# ── Motivation: how badly do they want to transact? (blueprint §33) ──────────
# These are the blueprint's own worked weights, kept verbatim so the document and
# the code cannot drift apart.

TIMELINE_POINTS  = {"UNDER_30_DAYS": 25, "30_TO_90_DAYS": 12, "3_TO_6_MONTHS": 2,
                    "JUST_EXPLORING": -20}
CONDITION_POINTS = {"MAJOR_REPAIRS": 10, "SOME_REPAIRS": 5, "GOOD": 0}
REASON_POINTS    = {"FORECLOSURE": 20, "RELOCATION": 12, "INHERITED": 12,
                    "DIVORCE": 12, "TIRED_LANDLORD": 10, "NEEDS_REPAIRS": 8,
                    "CURIOUS": -15}
PRIORITY_POINTS  = {"SELL_AS_IS": 8, "SPEED": 10, "MAX_PRICE": -10}


def motivation_score(answers, signals=None):
    """0-100 from the questionnaire, with vacancy as a corroborating signal."""
    signals = signals or {}
    drivers, total = [], 40      # baseline: they filled in the form at all

    def add(points, label):
        nonlocal total
        total += points
        drivers.append({"factor": label, "points": points})

    for code, table, label in (
        ("TIMELINE",  TIMELINE_POINTS,  "timeline"),
        ("CONDITION", CONDITION_POINTS, "condition"),
        ("SELL_REASON", REASON_POINTS,  "reason"),
        ("PRIORITY",  PRIORITY_POINTS,  "priority"),
    ):
        answer = answers.get(code)
        if answer in table:
            add(table[answer], f"{label}:{answer.lower()}")

    if answers.get("PRICE_FLEXIBLE") == "YES":  add(+15, "explicit_price_flexibility")
    if signals.get("vacant"):                   add(+12, "property_vacant")
    if signals.get("active_listing"):           add(-25, "already_listed_with_agent")

    return _clamp(total), drivers


# ── Property opportunity: is it worth an investor's time? ────────────────────

def property_score(features):
    """0-100 on the property economics. Depends on enrichment, so it is gated."""
    drivers, total = [], 30

    def add(points, label):
        nonlocal total
        total += points
        drivers.append({"factor": label, "points": points})

    value  = features.get("market_value") or 0
    equity = features.get("estimated_equity") or 0
    ratio  = (equity / value) if value else 0

    if   ratio >= 0.60: add(+30, "equity_ratio_over_60pct")
    elif ratio >= 0.40: add(+20, "equity_ratio_40_to_60pct")
    elif ratio >= 0.20: add(+10, "equity_ratio_20_to_40pct")
    elif value:         add(-15, "thin_or_negative_equity")

    if features.get("free_and_clear"):        add(+15, "free_and_clear")
    if features.get("absentee_owner"):        add(+10, "absentee_owner")
    if features.get("out_of_state_owner"):    add(+8,  "out_of_state_owner")
    if features.get("foreclosure_notice"):    add(+18, "active_foreclosure_notice")
    if features.get("code_violation"):        add(+8,  "code_enforcement_violation")
    if features.get("tax_delinquent"):        add(+10, "tax_delinquent")

    years = features.get("years_owned") or 0
    if years >= 10:                           add(+8, "owned_10_years_plus")

    if features.get("entity_owned"):          add(+5, "entity_owned")

    return _clamp(total), drivers


# ── The staleness gate [REVIEW F3] ──────────────────────────────────────────

def check_scoreable(score_type, domain_freshness):
    """Return (ok, stale_domains). A score depending on stale data is not emitted.

    This is the amendment to the blueprint. Without it, enrichment that has been
    dark for eleven days is snapshotted at full confidence, scored, and routed —
    and the audit trail makes the resulting number reproducible without making it
    correct.
    """
    stale = [
        domain for domain in SCORE_DEPENDENCIES.get(score_type, [])
        if domain_freshness.get(domain, "MISSING") not in FRESH_ENOUGH
    ]
    return (not stale), stale


def master_score(trust, motivation, prop, market, domain_freshness=None):
    """Weighted composite. Returns None when its inputs cannot be trusted."""
    domain_freshness = domain_freshness or {}
    ok, stale = check_scoreable("MASTER", domain_freshness)
    if not ok:
        return None, {"suppressed_reason": "STALE_ENRICHMENT", "stale_domains": stale,
                      "score_version": f"MASTER_SCORE_{SCORE_VERSION}"}

    total = (motivation * MASTER_WEIGHTS["motivation"]
             + prop     * MASTER_WEIGHTS["property"]
             + trust    * MASTER_WEIGHTS["trust"]
             + market   * MASTER_WEIGHTS["market"])
    return round(total, 2), {
        "score_version": f"MASTER_SCORE_{SCORE_VERSION}",
        "weights": MASTER_WEIGHTS,
        "components": {"trust": trust, "motivation": motivation,
                       "property": prop, "market": market},
    }


# ── Priority (blueprint §36): operators act on P0, not on 83.47 ─────────────

def priority(master, trust):
    if master is None:      return "P2"   # unscoreable goes to the review queue
    if trust < 40:          return "P3"   # never prioritise an untrusted lead
    if master >= 80:        return "P0"
    if master >= 65:        return "P1"
    if master >= 45:        return "P2"
    return "P3"


def deal_probability(*_args, **_kwargs):
    """Blueprint §34: not until labelled outcomes exist. Deliberately unbuilt."""
    return None, {"suppressed_reason": "NOT_ENOUGH_DATA"}


def _clamp(value, low=0, high=100):
    return max(low, min(high, value))


# ── Tests ───────────────────────────────────────────────────────────────────

FRESH_ALL = {"property": "FRESH", "financial": "FRESH",
             "distress": "FRESH", "market": "FRESH"}


def run_tests():
    failures = []
    checks = 0

    def check(label, got, want):
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")
        else:
            print(f"  pass  {label}")

    # Trust
    t_good, _ = trust_score({"phone_verified": True, "address_resolved_exact": True,
                             "owner_name_matches": True})
    check("trust: verified + matched owner scores high", t_good >= 85, True)

    t_bad, _ = trust_score({"phone_verified": False, "fraud_probability": 0.8})
    check("trust: unverified + fraud signal floors at 0", t_bad, 0)

    t_dup, _ = trust_score({"phone_verified": True, "duplicate_probability": 0.97})
    check("trust: near-certain duplicate is heavily penalised", t_dup < 40, True)

    # Motivation
    m_hot, drivers = motivation_score(
        {"TIMELINE": "UNDER_30_DAYS", "CONDITION": "MAJOR_REPAIRS",
         "SELL_REASON": "FORECLOSURE", "PRIORITY": "SPEED", "PRICE_FLEXIBLE": "YES"},
        {"vacant": True})
    check("motivation: distressed urgent seller scores very high", m_hot >= 90, True)
    check("motivation: drivers are returned for explainability", len(drivers) >= 5, True)

    m_cold, _ = motivation_score({"TIMELINE": "JUST_EXPLORING", "PRIORITY": "MAX_PRICE",
                                  "SELL_REASON": "CURIOUS"})
    check("motivation: tyre-kicker scores low", m_cold <= 20, True)

    m_listed, _ = motivation_score({"TIMELINE": "UNDER_30_DAYS"}, {"active_listing": True})
    check("motivation: already listed with an agent is penalised",
          m_listed < motivation_score({"TIMELINE": "UNDER_30_DAYS"})[0], True)

    # Property
    p_great, _ = property_score({"market_value": 300000, "estimated_equity": 220000,
                                 "free_and_clear": True, "absentee_owner": True,
                                 "foreclosure_notice": True, "years_owned": 12})
    check("property: free-and-clear absentee in foreclosure scores very high",
          p_great >= 90, True)

    p_thin, _ = property_score({"market_value": 300000, "estimated_equity": 10000})
    check("property: thin equity is penalised", p_thin < 30, True)

    # [REVIEW F3] the gate
    ok, stale = check_scoreable("MASTER", {**FRESH_ALL, "distress": "STALE"})
    check("gate: a stale domain blocks MASTER", (ok, stale), (False, ["distress"]))

    ok2, _ = check_scoreable("MOTIVATION", {"distress": "STALE"})
    check("gate: MOTIVATION has no enrichment deps, so stays scoreable", ok2, True)

    ok3, stale3 = check_scoreable("MASTER", {})
    check("gate: absent freshness is treated as MISSING, not assumed fresh",
          (ok3, len(stale3)), (False, 4))

    score, meta = master_score(90, 90, 90, 50, {**FRESH_ALL, "financial": "MISSING"})
    check("gate: master returns None when suppressed", score, None)
    check("gate: suppression reason is recorded", meta["suppressed_reason"], "STALE_ENRICHMENT")

    # Master + priority
    good, meta = master_score(88, 92, 90, 60, FRESH_ALL)
    check("master: weighted composite computes", good, round(92*.40 + 90*.30 + 88*.20 + 60*.10, 2))
    check("master: version is stamped", meta["score_version"], "MASTER_SCORE_V1")
    check("priority: high master with high trust is P0", priority(good, 88), "P0")
    check("priority: low trust can never be prioritised", priority(95, 30), "P3")
    check("priority: a suppressed score routes to review, not to the top",
          priority(None, 90), "P2")

    # §34
    dp, dp_meta = deal_probability()
    check("deal probability: deliberately not built in v1",
          (dp, dp_meta["suppressed_reason"]), (None, "NOT_ENOUGH_DATA"))

    check("master weights sum to 1.0", round(sum(MASTER_WEIGHTS.values()), 6), 1.0)

    if failures:
        print(f"\n{len(failures)} FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"\nall {checks} scoring checks pass")
    return 0


def demo():
    cases = [
        ("Urgent distressed seller, all data fresh",
         {"phone_verified": True, "address_resolved_exact": True, "owner_name_matches": True},
         {"TIMELINE": "UNDER_30_DAYS", "CONDITION": "MAJOR_REPAIRS",
          "SELL_REASON": "FORECLOSURE", "PRIORITY": "SPEED"},
         {"market_value": 269400, "estimated_equity": 200000, "absentee_owner": True,
          "foreclosure_notice": True, "years_owned": 11},
         FRESH_ALL),
        ("Same seller, but distress feed has been dark 11 days",
         {"phone_verified": True, "address_resolved_exact": True, "owner_name_matches": True},
         {"TIMELINE": "UNDER_30_DAYS", "CONDITION": "MAJOR_REPAIRS",
          "SELL_REASON": "FORECLOSURE", "PRIORITY": "SPEED"},
         {"market_value": 269400, "estimated_equity": 200000, "absentee_owner": True,
          "foreclosure_notice": True, "years_owned": 11},
         {**FRESH_ALL, "distress": "STALE"}),
        ("Curious homeowner, unverified phone",
         {"phone_verified": False},
         {"TIMELINE": "JUST_EXPLORING", "PRIORITY": "MAX_PRICE", "SELL_REASON": "CURIOUS"},
         {"market_value": 480000, "estimated_equity": 60000},
         FRESH_ALL),
    ]
    for label, tsig, answers, feats, fresh in cases:
        trust, _ = trust_score(tsig)
        motiv, _ = motivation_score(answers)
        prop, _  = property_score(feats)
        master, meta = master_score(trust, motiv, prop, 55, fresh)
        print(f"\n{label}")
        print(f"  trust {trust}   motivation {motiv}   property {prop}")
        if master is None:
            print(f"  MASTER  suppressed — {meta['suppressed_reason']} "
                  f"({', '.join(meta['stale_domains'])})")
            print(f"  routes to MANUAL_REVIEW, priority {priority(master, trust)}")
        else:
            print(f"  MASTER  {master}   priority {priority(master, trust)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        demo()
        sys.exit(0)
    sys.exit(run_tests())
