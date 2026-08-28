# Operations Log

Append-only timeline of ingests, queries, and lint passes.
Entry prefix format: `## [YYYY-MM-DD] operation | description`
(`grep "^## \[" log.md | tail -5` shows the last 5 operations.)

## [2026-07-08] init | Vault created

Scaffolded from Karpathy's LLM Wiki pattern: schema (CLAUDE.md), empty wiki
with index, immutable raw/ sources directory, and this log.

## [2026-08-28] query | Outstanding items across all sessions

Enumerated 72 sessions via the session API and filed the roll-up as
[[outstanding-items-2026-08-28]]. Only titles and post-turn summaries were
readable, not transcripts. Spun out three pages: [[rp2]], [[monitorclt]], and
[[charlotte-reia-calendar]].

## [2026-08-28] query | Verify the rp2 AUDIT.md correctness claims

Checked four claims from PR #3 against the code; filed
[[rp2-audit-verification]]. Two confirmed and fixed (US long-term holding
period, JP report year ordering), one confirmed but narrower than claimed
(ODS first-row skip), one mischaracterized as security (formula injection).
