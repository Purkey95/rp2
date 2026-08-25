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
