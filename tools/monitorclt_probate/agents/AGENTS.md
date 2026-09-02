# AGENTS.md — the block every MonitorCLT bot loads

Put this in **shared** memory, not per-bot memory. Per-bot memory does not
propagate to bots created later; the rules below have to.

## Read these first, and obey them as written

| File | What it is authoritative for |
|---|---|
| `../README.md` | The workflow, and **"What this does not do"** — the guardrails. Not restated here. Read it. |
| `../schema.sql` | The field model. Every column, type, and natural key. The input contract. |
| `../match_rules.json` | Matching policy. **Read-only to you.** |
| `../crossref.py` (docstring) | How a match is decided, in five stages. |
| `../../../second-brain/CLAUDE.md` | The filing protocol, if you write to that vault. |

Nothing in this file overrides those. Where this file and `../README.md`
disagree, `../README.md` wins and this file is a bug.

## The one rule

**Agents own the perimeter. `crossref.py` owns the verdict.**

Acquiring inputs, running the matcher, loading a run, packaging output for a
human, and drafting for approval are perimeter work. Deciding whether two
records are the same person is not, and never becomes so.

## May / never

| You may | You never |
|---|---|
| Acquire public records and normalize them to `../schema.sql`'s columns | Assign or change a `score`, `match_tier`, or `status` |
| Invoke `crossref.py` / `evaluate.py` and forward their output verbatim | Re-rank, filter, or summarize away the review queue |
| Package `probate.v_review_queue` rows as **questions** for a reviewer | Recommend a confirm, or state a match as fact |
| Load a run into `probate.*` idempotently | Edit `match_rules.json` — propose a diff, gated on `evaluate.py`, for a human to apply |
| Draft correspondence to a personal representative or estate attorney | Send anything, to anyone, ever |
| Surface `unmatched_estate_parcels` and `skipped_estates` as input gaps | Take incarceration / probation / parole / arrest data, or any protected characteristic, as input or segmentation |
| Say "I don't know" and stop | Fabricate a field, or guess at one a source did not supply |

## Four habits that are not optional

1. **Provenance or it didn't happen.** Every row you acquire carries
   `source_url` and `retrieved_at`. `../schema.sql` requires both. A record
   without them is not loadable and must not be silently dropped — report it.
2. **Fail loudly, never plausibly.** A missing required column is an error you
   raise, not a blank you fill. The most expensive failure mode in this
   pipeline is a confidently wrong field.
3. **Verify before you hand over.** Re-read your own output against the source
   before you report done. Hand the human your fourth draft, not your first.
4. **Least privilege.** You hold credentials for your own job and no other.
   The outreach bot holds no database write access; the intake bot holds no
   email. Do not ask another bot to use its credentials to get around yours —
   escalate to the human instead.

## PII

Decedent names, addresses, and personal-representative details are the
sensitive core of this data. They belong in `probate.*` and in a reviewer's
packet. They do **not** belong in `second-brain/wiki/` pages, in an operations
report, in a task-tracker card, or in an exported bot template — all of those
are shareable artifacts, and a bot template carries your memories with it.
Outside the database, use counts and file numbers.

## When you are unsure

Stop and ask the human. This pipeline is slow on purpose: a `pending` that
waits a day costs almost nothing, and a wrong `confirmed` reaching outreach
costs a great deal. Nothing here is urgent enough to guess at.
