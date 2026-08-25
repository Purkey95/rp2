---
type: answer
created: 2026-08-25
updated: 2026-08-25
tags: [ai, agents, monitorclt, tooling]
---

# Is the "agentic harness" course worth anything to MonitorCLT?

**Source:** a Khairallah AL-Awady thread (2026-08-12), "How to Build a $10K/Month
Agentic Harness using Kimi K3." Promotional in framing, but its factual claims
about the model check out: Moonshot released K3's weights on 2026-07-27 — a
2.8T-parameter MoE model, 1M-token context, native vision, always-on reasoning,
and the largest open-weight model released to date.

**Verdict: the diagnosis is worth a lot; the product recommendation is worth
little right now.**

## Why the diagnosis lands

MonitorCLT is *already* a harness — just a non-LLM one. It has the loop (53
scheduled pipelines), the tools, the memory (SQLite + Follow Up Boss), and the
observation channel (155 metrics, daily digests). What it does not have is the
one thing the article correctly identifies as load-bearing: **a success condition
with a verifier attached.**

The proof is in its own digest. `rod_lending_ocr` sat at 0% success for eleven
days. FSBO Match went dark for twelve. GDELT for ten. The system *detected* every
one of those and *reported* them — into an inbox where the alerts sit unread. The
loop runs; nothing closes it. That is precisely the failure the article describes,
and the disk at 90% is the same failure in physical form: no budget cap.

## The three ideas worth stealing

1. **The verifier that didn't do the work.** The article's best point. Apply it in
   two places: a check that reopens a pipeline when its success rate hits zero,
   and — more importantly — a numbers-check on anything published externally.
   Under [[marketing-strategy-monitorclt]], MonitorCLT's data goes public and to
   reporters. One wrong foreclosure count quoted in the Observer costs more
   credibility than the channel earns in a year. A verifier that re-derives every
   published figure from the database before the report ships is cheap insurance.
2. **Hard budgets** — step, time, and cost caps, plus disk. Non-negotiable for
   anything unattended.
3. **A project constitution.** Already the pattern in this vault's `CLAUDE.md`;
   worth extending to the MonitorCLT repo if it lacks one.

Routing, the article's cost section, MonitorCLT is already doing right: local
Ollama scores news sentiment. Do not put a frontier model on a 79-row scrape.

## Where it does not apply

- **Nothing currently broken in MonitorCLT is a model-capability problem.** Dead
  cron jobs, a full disk, failed OCR, and 85% of leads missing a phone number are
  ops problems. A better model does not restart a dead cron. Switching stacks to
  Kimi Code would be a lateral move with real migration cost and no fix attached.
- **The "$10K/month" framing is engagement bait.** Ignore it.
- **Data governance deserves a deliberate decision.** MonitorCLT handles
  distressed-homeowner PII — names, addresses, foreclosure status — plus CRM
  records. Routing that through any foreign-hosted API is a choice to make on
  purpose, not by default. K3's open weights permit self-hosting in principle,
  but 2.8T parameters needs a multi-GPU server, and the current box is 90% full
  of a 245GB disk.

## Where a harness *would* pay, concretely

Adopt the pattern as the engine that executes the marketing strategy, not as a
stack change:

1. **Self-healing pipeline agent** — on N days at 0% success, read the log,
   attempt a fix, re-run, revert if worse, report the diff. Aimed straight at the
   top operational problem.
2. **Report-and-page generation agent** — turns pipeline output into the weekly
   market report and the ZIP/rezoning pages of Pillars 1 and 3, with the verifier
   above checking every figure. This is the marketing multiplier.
3. **Contact-enrichment agent** — NC Secretary of State lookups on entity-owned
   leads. Mechanical, high-volume, cheap-model work that attacks the 15.09%
   contactable rate directly.

Related: [[monitorclt]] · [[marketing-strategy-monitorclt]]
