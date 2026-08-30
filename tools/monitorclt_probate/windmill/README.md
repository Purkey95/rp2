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

### 1. Three login roles in PostgreSQL

The migrations create `probate_loader`, `probate_reviewer` and `probate_outreach`
as `NOLOGIN` **group** roles — they carry the privileges, they are not accounts.
Windmill needs something it can connect as:

```sql
CREATE ROLE wm_loader   LOGIN PASSWORD '...';
CREATE ROLE wm_reviewer LOGIN PASSWORD '...';
CREATE ROLE wm_outreach LOGIN PASSWORD '...';

GRANT probate_loader   TO wm_loader;
GRANT probate_reviewer TO wm_reviewer;
GRANT probate_outreach TO wm_outreach;
```

Three logins, not one — a shared connection would make the separation in
`0003_rls.sql` decorative.

**On the audit trail:** `probate.current_reviewer()` prefers the JWT's email
claim and falls back to `session_user`. A person reviewing through Supabase's
API signs the log with their own address; a person reviewing over `psql` as
`wm_reviewer` signs it `wm_reviewer`. If more than one human clears the queue,
route them through the API or give each their own login — otherwise the log
records that *someone* decided, which is not what it is for.

### 2. One Variable and three Resources

| Path | Kind | Holds |
|---|---|---|
| `f/monitorclt/probate_match_rules` | Variable, **not secret** | `match_rules.json` |
| `f/monitorclt/probate_db_loader` | Resource, `postgresql` | `wm_loader` |
| `f/monitorclt/probate_db_reviewer` | Resource, `postgresql` | `wm_reviewer` |
| `f/monitorclt/probate_db_outreach` | Resource, `postgresql` | `wm_outreach` |

That is the whole list. `sync.sh` generates the variable spec from
`../match_rules.json`, so pushing it is not a separate step and the two cannot
drift. It is deliberately **not secret**: Windmill defaults new variables to
secret, but these weights decide who ends up on a lead list — that is policy, and
policy that cannot be read cannot be reviewed. Nothing in the file is a
credential.

The resources are, so they are not generated and not in git. Copy the templates,
fill in the passwords, push:

```bash
cp resources.example/*.resource.yaml f/monitorclt/
$EDITOR f/monitorclt/probate_db_*.resource.yaml
for role in loader reviewer outreach; do
    wmill resource push f/monitorclt/probate_db_$role.resource.yaml \
                        f/monitorclt/probate_db_$role
done
```

(Or create them in the UI: Resources → Add resource → PostgreSQL. Either way
`f/monitorclt/*.resource.yaml` is gitignored.)

### 3. Push and schedule

```bash
npm install -g windmill-cli
wmill workspace add monitorclt <workspace-id> https://<your-windmill>
./sync.sh          # stages crossref.py, load_run.py and the variable, then pushes
```

Then schedule `f/monitorclt/probate_crossref` weekly per county, and give it the
three resources as flow inputs — `loader_database`, `reviewer_database`,
`outreach_database`.

To change a weight after calibration: edit `../match_rules.json`, re-run
`./sync.sh`. The variable moves with it, and Windmill keeps the version history.

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
