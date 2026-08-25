# Implementation Dependency Graph

What can be built in parallel, what genuinely blocks what, and where the gates
fall. Arrows mean "must exist before".

```mermaid
graph TD
    Gm1["G−1 · Fix the leak<br/>contactability 50% · 302 leads worked<br/>CAN-SPAM set · rod_lending_ocr repaired"]:::gate

    G0["G0 · Freeze contracts<br/>schema · events · APIs"]:::gate

    Gm1 --> G0

    G0 --> DB[("01 Schema<br/>35 tables")]
    G0 --> EV["02 Event bus<br/>+ envelope"]
    G0 --> SM["04 State machines"]

    DB --> INTAKE["Seller Intake<br/>+ session/property/contact"]
    EV --> INTAKE
    SM --> INTAKE

    INTAKE --> OTP["Verification<br/>OTP + rate limits"]
    INTAKE --> CONSENT["Consent capture<br/>onward disclosure"]:::legal

    OTP --> G2["G2 · OTP + consent validated"]:::gate
    CONSENT --> G2

    G2 --> RESOLVE["Property Resolution<br/>address → property_id"]
    RESOLVE --> G3["G3 · Resolution reliable"]:::gate

    G3 --> ENRICH["Enrichment Orchestrator"]
    FRESH["Freshness SLA layer<br/>REVIEW F3"]:::amend --> ENRICH
    ENRICH --> G4["G4 · Enrichment connected"]:::gate

    G4 --> QUALITY["Lead Quality<br/>10 checks + dedupe"]
    G4 --> SCORE["05 Scoring<br/>trust/motivation/property/master"]
    FRESH --> SCORE
    QUALITY --> G5["G5 · QC + scoring working"]:::gate
    SCORE --> G5

    G5 --> OPP["Opportunity Service<br/>+ priority"]
    OPP --> OPS["06 Ops Console<br/>inbox · review · detail"]
    OPS --> G6["G6 · Ops console"]:::gate

    G6 --> FUB["Follow Up Boss sync<br/>REVIEW F2"]:::amend
    FUB --> OUTCOME["Outcome tracking<br/>activity · contract · closing"]
    OUTCOME --> G7["G7 · CRM + outcomes"]:::gate

    ORGANIC["Organic funnel top<br/>reports · press · alert list<br/>REVIEW F5"]:::amend --> G8
    LEGAL["NC attorney review<br/>G.S. 75-120 · TCPA · disclosure<br/>REVIEW F4"]:::legal --> G8
    G7 --> G8["G8 · Pilot on organic only<br/>NO ad spend"]:::gate

    G8 --> ATTRIB["Attribution rollup<br/>cost per stage"]
    ATTRIB --> G9["G9 · Economic validation"]:::gate

    G9 --> G95["G9.5 · Decide on paid acquisition<br/>REVIEW F5"]:::gate
    G95 --> G10["G10 · External marketplace<br/>routing · wallet · refunds<br/>SPECIFIED, NOT BUILT"]:::gate

    classDef gate fill:#1B4965,stroke:#0d2b3d,color:#fff
    classDef amend fill:#B3541E,stroke:#7d3a15,color:#fff
    classDef legal fill:#B3261E,stroke:#7d1a15,color:#fff
```

## The critical path

`G−1 → schema → intake → OTP/consent → resolution → enrichment → scoring →
opportunity → ops console → FUB → outcomes → pilot`.

Everything else is parallelisable. Three things are commonly assumed to be on
the critical path and are not:

- **The marketplace** (routing, wallet, refunds, buyer portal). Specified at G0
  so the contracts are stable, built at G10 or never. Roughly 40% of the
  blueprint's surface area sits behind a gate most businesses never reach.
- **ML scoring.** Deal Probability stays `NOT_ENOUGH_DATA` until labelled
  outcomes exist. The model registry is an empty table until G9.
- **Twenty CRM.** Replaced by Follow Up Boss for v1 per F2, removing a
  migration from the critical path entirely.

## What can start immediately, in parallel with G−1

- The schema and event catalog are already written and validated here.
- The organic funnel top — reports, press list, alert list — is the marketing
  strategy, needs no engineering from this spec, and is what makes G8 affordable.
- The NC attorney review. It has the longest external lead time of anything in
  the programme and gates G8. **Start it now, not at G7.**

## Longest pole

Not the code. The attorney review and the accumulation of enough labelled
outcomes to make §86's loop mean anything. Both are calendar time, not
engineering time, and both start earlier than the graph's arrows suggest.
