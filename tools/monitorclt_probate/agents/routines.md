# Cadence

Every routine names the bot that owns it and the guard that stops it doing
damage when something upstream is wrong. A routine without a guard is a way to
be wrong on a schedule.

| Trigger | What runs | Bot | Guard |
|---|---|---|---|
| Weekday 06:00 | `intake-records` — estate cases, per county | Intake | Missing required column is an error, not a blank. Report and stop; do not partially load |
| Weekly Mon 05:00 | `intake-records` — parcels, deeds | Intake | Assessor and register data do not change daily; pulling them daily is cost with no signal |
| **On successful intake** | `crossref.py` → `load-run` → file to vault | Runner, then Scribe | Only on success. A run over a partial pull produces `pending` rows that look like real questions |
| Weekday 08:00 | `triage-review-queue`, only if `v_review_queue` is non-empty | Queue | Silent when empty. Escalate any `pending` older than N days rather than re-sending the same packet |
| Monthly | `calibrate-proposal` | Calibrator | Output is a proposal. Nothing applies it but a human |
| Weekly Fri | Forward the last `format_report()` + cross-run trend | Scribe | Trend only. Do not re-render the report |
| — | `draft-pr-outreach` | Counsel-draft | **No routine.** Human-initiated, per estate, after verification |

## Why the run is event-chained

Cross-referencing stale input is worse than not running: it produces a queue of
`pending` rows a reviewer will work through, whose real cause is that intake
failed. Chain the run to a successful intake and a broken pull shows up as *no
run*, which is obvious, instead of *a bad run*, which is not.

## Why nothing here triggers outreach

The workflow in `../README.md` has four steps, and step 3 — verify chain of
title, liens, heirs, and the representative's authority — is a human step with
no automated equivalent. A routine that ran step 4 would be skipping step 3 by
construction. `confirmed` is a records match, not a conclusion.

## Escalation

- `pending` older than N days (start at 14) → Queue raises it with Clerk once,
  then stops. Repeated identical nags train people to ignore packets.
- Intake failing twice in a row for the same county → Clerk raises it with the
  human. Do not silently retry a third time.
- `unmatched_estate_parcels` growing run over run → an intake coverage gap, not
  a matching problem. Scribe surfaces it in the weekly trend.
- A calibration proposal outstanding for two cycles → Clerk raises it. An
  unapplied proposal means the matcher is running on weights nobody has
  reaffirmed.

## Cost

Cadence is where an agent system quietly gets expensive. The two rules that
matter: pull on the rhythm the source actually publishes on (see
[`sources.md`](sources.md)), and let deterministic code do the parsing. A model
reading portal HTML row by row is the single largest avoidable cost here, and
also the largest correctness risk.
