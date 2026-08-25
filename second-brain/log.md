# Operations Log

Append-only timeline of ingests, queries, and lint passes.
Entry prefix format: `## [YYYY-MM-DD] operation | description`
(`grep "^## \[" log.md | tail -5` shows the last 5 operations.)

## [2026-07-08] init | Vault created

Scaffolded from Karpathy's LLM Wiki pattern: schema (CLAUDE.md), empty wiki
with index, immutable raw/ sources directory, and this log.

## [2026-08-25] query | Best zero-cost marketing strategy from MonitorCLT data

Read MonitorCLT alert emails (morning brief, metrics, data quality, foreclosure
notices) and the Daily Research Brief market-signals block. Filed the answer as
[[marketing-strategy-monitorclt]], plus supporting pages [[monitorclt]] and
[[charlotte-market-signals-2026-08]]. Key finding: the data is non-commodity and
unmatched locally, but the funnel leaks (85% of drip enrollments uncontactable,
302 hot leads untouched, four pipelines dead 10+ days, CAN-SPAM address unset) —
fix those before opening any new channel.

## [2026-08-25] query | RSS feeds for the six Charlotte housing outlets

Probed native feeds for the Observer, Axios Charlotte, WFAE, the Ledger, the
Business Journal, and QCity Metro. Only WFAE (section feeds, not the empty root)
and QCity Metro serve usable RSS to a server; the other four block datacenter
fetches with Cloudflare or connection resets. Google News `site:` feeds cover all
four, verified returning genuine per-outlet headlines. Filed as
[[charlotte-news-feeds]] with config and a verifier in
`tools/charlotte-news-feeds/`. All 8 feeds healthy at time of writing.

## [2026-08-25] build | Charlotte news monitor wired up

Built `tools/charlotte-news-feeds/news_monitor.py` to replace MonitorCLT's dead
GDELT job: fetches the 8 verified feeds, scores headlines against seven weighted
housing topics with a locality gate, stores to SQLite, and promotes pitchable
stories with the MonitorCLT asset that answers each. Four bugs found and fixed
during testing (unstable `hash()` breaking dedupe, fetch-date rather than
publish-date windowing, "zoning" matching inside "rezoning", and word boundaries
rejecting plurals). Regression tests pinned in `test_scoring.py`, 7/7 passing.

## [2026-08-25] decision | News monitor scheduled; three agent tools tabled

Scheduled the Charlotte news monitor as a daily Routine (trig_01KWSPm9gzRYvnK218fNYrZT,
07:04 ET, push delivery — connectors are org-gated so the fired session has no mail
tools). Added `--only-if-actionable` so quiet days stay silent. Tabled Kimi Code/K3,
gods-eye-view, and CopilotKit/openbot with reasons recorded in
[[agentic-harness-assessment]] so the question is not re-litigated later.

## [2026-08-25] query | Does the Seller Engine blueprint combine with the marketing strategy?

Reviewed Technical Blueprint v2.0 (87 sections, 15 locked decisions) against
MonitorCLT's current operational state. Verdict: adopt with amendments — the
architecture is sound, but its funnel begins with paid ads while the marketing
strategy was built to a zero-budget constraint. Swapping the funnel top for
organic keeps everything downstream intact and makes the §85 economics gate
passable at low volume. Five findings filed in [[seller-engine-blueprint-review]],
including a stale-enrichment scoring guard and an NC compliance gap (G.S. 75-120,
TCPA) that needs attorney review before G8, not G10.

## [2026-08-25] build | Seller Engine Data & Workflow Specification v1.0

Wrote the pre-code specification in `tools/seller-engine-spec/`: 35 tables, 22
events, API contracts, executable state machines and scoring formulas,
enrichment SLAs, wireframes, operations rules, dependency graph. Verified rather
than asserted — the DDL executes against real PostgreSQL 16 (36 tables), four
integrity guards were exercised with live inserts, and validate_spec.py runs 13
cross-checks including SQL enums against the Python state machines. Blueprint
review findings F2/F3/F4 are folded into the contracts. Filed as
[[seller-engine-spec]].
