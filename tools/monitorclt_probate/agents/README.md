# An agent layer for MonitorCLT

MonitorCLT is a matcher and nothing else. `crossref.py` answers one question —
*does this estate appear to hold real property, and where* — deterministically,
with its evidence attached. What surrounds it is missing entirely: nothing
acquires the estate, parcel and deed records it reads (the only inputs in the
repo are the synthetic files in `../sample/`), nothing loads a run into the
schema `../schema.sql` defines, nothing routes the review queue to a human,
nothing runs `evaluate.py` on a cadence, and nothing drafts the outreach the
workflow ends in. The tool works and is fed by hand.

Those five gaps are all **perimeter** work — acquire, orchestrate, package,
draft — and none of them is judgment about whether two records are the same
person. That is what makes an agent layer safe here, and it is the whole design:

> **Agents own the perimeter. `crossref.py` owns the verdict.**

An LLM never scores a candidate, never sets a status, never re-ranks the queue,
never edits `../match_rules.json` without a labeled-eval gate and a human, and
never sends anything. `AGENTS.md` states this as instructions; every bot loads
it from shared memory.

## What this layer does not duplicate

The repo already says a lot, and saying it again in a second file is how the
second file starts lying. The rule for everything in this directory:

**Single source of truth — reference, don't restate.**

| Already authoritative | Where |
|---|---|
| The guardrails ("What this does not do") | `../README.md` — and repeated in `../schema.sql`'s header and printed at the foot of *every* `crossref.py` run |
| The field model: columns, types, natural keys | `../schema.sql` |
| How a match is decided | `../crossref.py` docstring + `../match_rules.json` |
| How to read an `evaluate.py` report | `../README.md`, "Calibrate before trusting the numbers" |
| How to file into the knowledge vault | `../../../second-brain/CLAUDE.md` |

Files here cite those by path and add only what is absent. In particular there
is no JSON Schema for the input records — `../schema.sql` already declares every
field — and no document mapping `crossref.py` output onto `probate.entity_match`,
because `crossref.py` already emits those column names verbatim.

## The four C's, applied

The framework is Context, Connections, Capabilities, Cadence. Applied to a
records pipeline with legal exposure, each one lands somewhere specific.

### Context

Every bot loads `AGENTS.md`, which points at `../README.md` and, for
data-touching bots, `../schema.sql` and `../match_rules.json` — read-only.

The one genuinely new context artifact is [`sources.md`](sources.md): per county,
what the systems of record are, how they publish, how often, and on what terms.
Nothing in the repo names a county source today. `crossref.py` already asks for
this file every run — `unmatched_estate_parcels` is precisely the list of
estate-marked parcels whose estate case is in a county or date range nobody
pulled.

Context that is *shared* propagates to bots created later; context in a single
bot's memory does not. The guardrails go in shared memory. Source quirks —
this county's assessor writes `LAST FIRST MIDDLE`, that one paginates at 250 —
belong to the intake bot.

### Connections

Read: county bulk and open-data endpoints first, portals only where no bulk
source exists. Write: `probate.*`, this repo, a task tracker, and **email drafts
only**.

Least privilege per bot is a hard requirement, and a deliberate departure from
the one-shared-computer model these agent platforms default to. A single shared
browser profile holding county portal logins, database credentials and a mail
account means every bot can do everything, and an outreach bot one prompt away
from `DELETE FROM entity_match` is not a system anyone should run against public
records about dead people. The outreach bot holds no database write access. The
intake bot holds no email.

Every acquired row carries `source_url` and `retrieved_at`. `../schema.sql`
requires both, and they are what lets a reviewer walk a match back to the public
record it came from.

### Capabilities

Five skills in [`skills/`](skills/), which is the count of things that have no
implementation today:

| Skill | Wraps | Why it exists |
|---|---|---|
| [`intake-records`](skills/intake-records.md) | nothing — new | Acquire and normalize estate cases, parcels, deeds |
| [`load-run`](skills/load-run.md) | nothing — new | Run JSON → `probate.*`, idempotently |
| [`triage-review-queue`](skills/triage-review-queue.md) | `probate.v_review_queue` | Turn pending rows into questions a human can answer |
| [`calibrate-proposal`](skills/calibrate-proposal.md) | `evaluate.py` | Proposed rule diffs, never applied |
| [`draft-pr-outreach`](skills/draft-pr-outreach.md) | `probate.v_estate_property` | Drafts only, never sent |

There is no `run-crossref` skill: it is one command line, already documented in
`../README.md`, and it belongs to the Runner bot rather than to a recipe. There
is no weekly-report skill either, because `format_report()` already prints run
counts, the per-estate rollup with each representative, per-link tier, evidence
and flags, `skipped_estates`, and `unmatched_estate_parcels`. An agent
re-rendering that output is strictly worse than piping it.

### Cadence

[`routines.md`](routines.md). The shape worth noting: the run is **event-chained
to a successful intake**, not clock-driven, because a run over stale input is
noise; and **outreach has no routine at all**, because the workflow in
`../README.md` puts a verification step and an authorized human between a
confirmed match and any contact.

## The roster

A coordinator and five operators — narrow bots with one job each, not one
agent that does everything. [`bots.md`](bots.md) has the full specification.

```
                    human
                      │
                   Clerk  ── coordinator; no credentials, no matching
                      │
   ┌──────────┬───────┼────────┬────────────┐
 Intake    Runner   Queue  Calibrator    Scribe        Counsel-draft
 acquire   crossref  review  evaluate.py   files &      drafts only,
 normalize + load    packets proposals     reports      built last
```

## Rollout

Incrementally, with a gate on each phase. Standing up all six bots on day one
produces a system nobody can debug and nobody should trust.

| Phase | Ships | Gate before proceeding |
|---|---|---|
| 0 | The specs in this directory. No automation | Roster agreed; `AGENTS.md` loaded into shared memory |
| 1 | Intake — one county, one source (estate cases), human-triggered | Two weeks; a human spot-checks 20 records against the source and finds zero fabricated or dropped fields |
| 2 | Runner | A loaded run is identical to a hand-run of `crossref.py`; the confirmed-row `CHECK` question in `skills/load-run.md` is settled |
| 3 | Queue triage | The reviewer confirms the packet saved time and never nudged toward a confirm |
| 4 | Calibration proposals | A proposal is adopted only after `evaluate.py` holds target precision on a label set that **grew** since the last change |
| 5 | Outreach drafts | NC real-estate/probate attorney sign-off on both template and process, per `../README.md` |

Nothing in phase 5 is automated even after it ships. Drafting is human-initiated
and sending is human-performed, permanently.

## Known risks

- **Browser-scraping county sites at volume is the wrong tool.** Deterministic
  adapters should parse; the agent should orchestrate. A model reading portal
  HTML row by row is expensive, silently lossy, and the likeliest place a
  fabricated field enters the pipeline. See `sources.md`.
- **A coordinator that "helpfully summarizes" the queue** is the subtlest
  failure here. It moves the disposition into the model without anyone deciding
  to. The queue is forwarded, not digested.
- **Shareable artifacts leak PII.** Wiki pages, ops reports, task cards and
  exported bot templates all travel; bot templates carry memories with them.
  Counts and file numbers outside the database.
- **Calibrating against a static label set** is tuning to the test. The gate in
  phase 4 requires the label set to have grown.

## Running it

[`grokbot.md`](grokbot.md) maps this onto Grokbot — bots, plugins, skills,
routines — and onto the portable fallback (`claude` in this directory plus
`cron`), since everything here is markdown and shell-invocable code.
