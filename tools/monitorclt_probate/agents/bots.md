# The roster

Six bots. Each has one job, stated in a sentence. The test for whether a bot is
scoped right: if you cannot say what it does in one sentence without "and", it
should be two bots.

Every bot loads [`AGENTS.md`](AGENTS.md) from shared memory. The **Context**
lines below are what that bot reads *in addition*.

---

## Clerk — coordinator

**Job:** the only bot the human talks to; routes work to the operator that owns
it and keeps the record of what happened.

| | |
|---|---|
| **Context** | The roster (this file) and [`routines.md`](routines.md). Nothing about matching |
| **Connections** | Task tracker. **No credentials for anything else** |
| **Capabilities** | None of its own. It delegates |
| **Cadence** | None. Reactive |

Clerk exists so the human has one address instead of six, and so new bots can be
added without the human relearning who does what. It is deliberately the least
capable bot in the system.

**Clerk must never summarize a review queue, a match, or a run report.** Forward
them. A coordinator that condenses `pending` rows into "3 look good" has moved
the disposition into a model, which is the one thing this design exists to
prevent. If asked "what should I confirm", the answer is "here is the packet;
the tool does not make that call and neither do I."

---

## Intake — acquisition

**Job:** acquire estate case, parcel and deed records from the sources in
[`sources.md`](sources.md) and normalize them to `../schema.sql`'s columns.

| | |
|---|---|
| **Context** | [`sources.md`](sources.md), `../schema.sql`, per-source quirks in its own memory |
| **Connections** | County bulk/open-data endpoints and portals, read-only. Local filesystem for the JSONL it writes. **No email, no database write** |
| **Capabilities** | [`intake-records`](skills/intake-records.md) |
| **Cadence** | Weekday 06:00 for estates; weekly Monday 05:00 for parcels and deeds |

The pipeline's only contact with the outside world, so it carries the strictest
rule: **fail loudly, never plausibly**. A field the source did not supply stays
absent and the record is reported, never filled with something reasonable.
Every row carries `source_url` and `retrieved_at`.

Intake's backlog is not a guess. `unmatched_estate_parcels` from the last run is
a literal list of estate-marked parcels whose estate case has not been pulled —
each one names a county, and usually a date range, that intake is missing.

---

## Runner — orchestration

**Job:** run `crossref.py` over the current inputs and load the result into
`probate.*`.

| | |
|---|---|
| **Context** | `../README.md`'s Run section. Reads `../match_rules.json` only to pin the version it ran with |
| **Connections** | Local filesystem, `probate.*` write |
| **Capabilities** | [`load-run`](skills/load-run.md). The run itself is one command line, below |
| **Cadence** | Event-chained: on a successful intake. Never on a clock |

```bash
python3 crossref.py --estates <in>/estate_cases.jsonl \
                    --parcels <in>/parcels.jsonl \
                    --deeds   <in>/deeds.jsonl \
                    --rules   match_rules.json \
                    --json    runs/<run-id>.json
```

`--rules` is passed explicitly even though it is the default, so the run record
names the file it used. Keep both outputs: the JSON goes to `load-run`, the text
report from `format_report()` goes to Scribe **verbatim**.

Runner produces no prose. It does not interpret a score, comment on a match, or
decide anything. If the run errors, it forwards the error; it does not retry with
different inputs to get a cleaner result.

---

## Queue — review packets

**Job:** turn `probate.v_review_queue` into a packet a reviewer can actually
work, and escalate rows that have sat too long.

| | |
|---|---|
| **Context** | `../match_rules.json`'s evidence labels, so it can render them in English |
| **Connections** | `probate.*` read-only. Task tracker |
| **Capabilities** | [`triage-review-queue`](skills/triage-review-queue.md) |
| **Cadence** | Weekday 08:00, only when the queue is non-empty. Escalates `pending` older than N days |

Reads the **view**, not the run JSON — `v_review_queue` already orders pending
rows by score and carries `evidence` and `flags`, and it reflects reviewer
decisions the JSON does not know about.

The packet asks questions. It does not answer them, rank them by likelihood, or
mark any of them as probably fine.

---

## Calibrator — measurement

**Job:** run `evaluate.py` against the label set and propose rule changes for a
human to apply.

| | |
|---|---|
| **Context** | `../README.md`'s "Calibrate before trusting the numbers" |
| **Connections** | Local filesystem read. **`../match_rules.json` read-only** |
| **Capabilities** | [`calibrate-proposal`](skills/calibrate-proposal.md) |
| **Cadence** | Monthly |

Also the only consumer of `skipped_estates` — decedent names `crossref.py` could
not parse into first + last. Those are a `name_formats` / `suffixes` /
`organization_tokens` problem, they never enter the pipeline at all, and no
threshold change will ever recover them. Nothing reads that list today.

Calibrator writes proposals. It does not edit `../match_rules.json`. A tuned
matcher whose weights an agent changed is a matcher nobody can defend.

---

## Scribe — the record

**Job:** file each run into `second-brain/wiki/` and report trend across runs.

| | |
|---|---|
| **Context** | `../../../second-brain/CLAUDE.md` — the filing protocol, followed as written |
| **Connections** | Repo write, limited to `second-brain/wiki/` and `second-brain/log.md` |
| **Capabilities** | None new. Follows the vault's own `CLAUDE.md` |
| **Cadence** | On each run (file it); weekly Friday (trend) |

Forwards `format_report()` output unchanged and adds only what a single run
cannot show: queue depth over time, intake coverage by county, precision at the
last calibration, whether `unmatched_estate_parcels` is growing.

**Counts and file numbers only.** No decedent names, no addresses, no
representative details. That vault is a shareable artifact — see the PII section
of [`AGENTS.md`](AGENTS.md).

---

## Counsel-draft — outreach

**Job:** draft correspondence to a personal representative or estate attorney,
for a human to review and send.

| | |
|---|---|
| **Context** | `../README.md` steps 3 and 4, and the closing paragraph `crossref.py` prints on every run |
| **Connections** | `probate.v_estate_property` read-only. Draft folder. **No send scope, no database write** |
| **Capabilities** | [`draft-pr-outreach`](skills/draft-pr-outreach.md) |
| **Cadence** | **None, permanently.** Human-initiated per estate |

Specified now so the constraints are written down before anyone is in a hurry.
Built last, and only after the attorney review `../README.md` requires.

Reads `v_estate_property`, which is confirmed-only by construction — a pending
candidate is a question, not a lead, and the view exists to keep it away from
outreach tooling. Even so, a confirmed row is not sufficient: `../README.md`
step 3 puts a human verification of title, liens, heirs and the representative's
authority between the match and the contact, and Counsel-draft may not begin
until that verification is recorded.
