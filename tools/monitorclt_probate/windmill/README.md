# Windmill: the scheduled run and the review gate

The matcher runs fine from a terminal. What a terminal cannot give you is the
thing the workflow actually depends on: a **stop**. Step 3 of the documented
workflow is *verify before acting* — chain of title, liens, heirs, the
representative's authority — and a weekly cron with no gate in it quietly demotes
that to a suggestion, because nothing in the pipeline is waiting for it.

So the flow suspends. `review_gate` is a Windmill approval step: the run halts
there until a named person resumes it, and `publish_leads` — the only step that
hands anything to a human to act on — cannot run before that happens.

```
run_crossref ──▶ load_matches ──▶ review_gate ══▶ publish_leads
  matcher          Postgres        ⏸ SUSPEND        confirmed leads
  (stdlib)         (one txn)       one approval      only
```

## The four steps

| Step | Connects as | Does |
|---|---|---|
| `run_crossref` | — | Runs the matcher. Rules come from a Windmill Variable, not the image. |
| `load_matches` | `probate_loader` | Upserts sources, opens a `match_run`, writes candidates. One transaction. |
| `review_gate` | `probate_reviewer` | Summarises the queue, then **suspends the flow**. |
| `publish_leads` | `probate_outreach` | Reads `v_estate_property`. Confirmed only. |

Three different roles on purpose (`db/migrations/0003_rls.sql`). The step that
publishes leads holds no write privilege on any system of record, and the step
that writes cannot decide a match. Give each one its own `postgresql` resource.

`crossref` also carries a `stop_after_if`: zero estate cases or zero parcels ends
the run rather than loading it. A county feed that changes shape returns an empty
list far more often than it returns an error, and an empty run looks exactly like
a quiet week once it is in the database.

## What the approval request contains

Counts, scores, and the SQL to decide a candidate. **No decedent names, no
addresses, no PINs.** Approval notifications land in Slack channels and inboxes,
which are not access-controlled surfaces; the queue itself is one click away
behind a login, for the people who should see it.

The number worth reading on that screen is `auto_confirmed_unreviewed` — matches
the rules confirmed on their own, which reach outreach with nobody having looked
at them. That is the population `evaluate.py --target-precision` exists to keep
honest, and the approval screen is where you find out whether it still is.

Resuming does **not** decide the pending queue. It records that a person looked.
Decisions are made with `probate.record_review(...)`, which writes the audit row —
there is no other door, by permission.

## Setup

```bash
npm install -g windmill-cli
wmill workspace add monitorclt <workspace-id> https://<your-windmill>

# rules live as a Variable, so a weight change is an audited act by a named
# user rather than a silent redeploy
wmill variable create f/monitorclt/probate_match_rules \
    --value "$(cat ../match_rules.json)"

./sync.sh          # stages crossref.py + load_run.py, then `wmill sync push`
```

Then in the Windmill UI: create three `postgresql` resources (one per role), and
schedule `f/monitorclt/probate_crossref` weekly per county.

`sync.sh` copies `crossref.py` and `load_run.py` up from the tool directory at
push time and `.gitignore` keeps the copies out of git. They have one home. The
matcher a reviewer reads and the matcher a schedule runs have to be the same
file, or the evidence lists stop meaning anything.

## What is deliberately not here

**Acquisition.** The flow takes already-pulled records as input. Scraping a court
portal and deciding who owns a house are different failure modes and should not
share a retry policy — a retry storm against the Clerk's portal is a different
kind of problem than a bad join, and NC eCourts terms are their own question.
Pull with something built for it, then hand this flow the records.

**Anything that decides a match.** Windmill schedules the matcher and gates the
output. It does not score, and no step here can move a candidate to `confirmed`.
