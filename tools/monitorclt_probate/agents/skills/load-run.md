---
name: load-run
description: Load a crossref.py run JSON into the probate schema idempotently, without clobbering human review decisions. Use after a successful cross-reference run.
---

# load-run

Insert one `crossref.py` run into `probate.match_run` and `probate.entity_match`.

`../../README.md` calls this "a straight insert", and the field mapping genuinely
is straight — `crossref.py` emits `left_source`, `left_id`, `right_source`,
`right_id`, `match_tier`, `score`, `evidence`, `flags` and `status` under those
exact names, which are the `entity_match` column names. There is nothing to map
and no mapping document; read `../../schema.sql`.

What is **not** straight is re-running. Two constraints make this the one place
in the pipeline where a careless write destroys work.

## Constraint 1 — a confirmed row needs a signature

```sql
CHECK (status <> 'confirmed' OR person_id IS NOT NULL OR reviewer IS NOT NULL)
```

A bulk insert of a run's `confirmed` rows violates this: the matcher sets
`status = 'confirmed'` but supplies neither `person_id` nor `reviewer`. The
schema comment resolves it — *"a human's signature or an auditable rule run;
either way it must name a person record."*

**Settled:** the loader writes `reviewer` and leaves `person_id` and
`reviewed_at` null.

```
reviewer    = 'rules@<rules_version>/run:<match_run.id>'
reviewed_at = NULL
person_id   = NULL
```

Both values come from `result["run"]`, which already carries `tool_version` and
`rules_version`. This satisfies the check, records exactly which rule run made
the assertion, and keeps the three states distinguishable:

| | `reviewer` | `reviewed_at` |
|---|---|---|
| Machine-confirmed by the rules | `rules@…` | null |
| Confirmed by a human | their name | set |
| Pending | null | null |

`reviewed_at IS NULL AND reviewer LIKE 'rules@%'` is then a machine confirm that
no person has ever looked at — worth knowing, and impossible to ask if the
loader had invented a `person_id` instead.

Do **not** create `probate.person` rows here. `../../schema.sql` says a canonical
person exists only once a match has been confirmed, and an auto-confirm is the
matcher's assertion, not a human's. Identity is created when a reviewer confirms.

## Constraint 2 — re-running must not overwrite a review

```sql
UNIQUE (left_source, left_id, right_source, right_id)
```

The unique key does not include `run_id`, so the same estate/parcel pair across
two runs is one row. If a reviewer rejected that pair last week and this week's
run scores it `pending`, a naive upsert silently reopens a question a human
already answered — and if they confirmed it, an upsert can demote it.

**A human decision always wins.** On conflict:

| Existing row | Update | Preserve |
|---|---|---|
| `reviewed_at IS NOT NULL` (a human decided) | `run_id`, `score`, `match_tier`, `evidence`, `flags` | `status`, `reviewer`, `reviewed_at`, `review_note`, `person_id` |
| `reviewed_at IS NULL` (never reviewed) | everything, including `status` and the `rules@…` reviewer | — |

The reviewed row still gets fresh evidence, because new deeds change what the
matcher can see and a reviewer should be able to notice that their old decision
now rests on different facts. It does not get a fresh status.

Report every row where the new `status` differs from a preserved human `status`.
That list is the most interesting output of a re-run: it is where the matcher
and a person now disagree.

## The command

```bash
python3 load_run.py --run runs/<run-id>.json \
        --estates <in>/estate_cases.jsonl --parcels <in>/parcels.jsonl \
        --deeds <in>/deeds.jsonl --entities <in>/business_entities.jsonl > load.sql
psql "$DSN" -v ON_ERROR_STOP=1 -1 -f load.sql
```

`load_run.py` writes the SQL; `psql` runs it as one transaction. The loader
implements every rule on this page, including both constraints below; the
closing `SELECT` in the output is the disagreement list. Runner runs this
command and forwards what `psql` prints. It does not edit the SQL.

## Order

1. Insert `probate.match_run` from `result["run"]` — `tool_version`,
   `rules_version`, and `params` (the input paths, the county and date range,
   and the record counts already in `run`). Keep the returned `id`.
2. Upsert source records first: `estate_case`, `parcel`, `deed`. Each has a
   natural unique key; conflicts update the record and its `retrieved_at`.
   Source tables are systems of record and are never mutated *by matching* —
   refreshing them from their own source is not matching.
3. Upsert `entity_match` per the table above.
4. One transaction. A partial load leaves a queue that describes nothing real.

## Do not

- Change a `score`, `match_tier`, or `status` the matcher produced. Copy them.
- Filter rows. `rejected` rows load too — a re-run that promotes a rejected
  candidate to `pending` is exactly the signal the review queue exists to carry,
  and it is invisible if rejected rows were never stored.
- Load a partial run. If `crossref.py` errored, there is nothing to load.

## Verify

- Row counts match `len(result["matches"])`.
- Every `confirmed` row satisfies the CHECK.
- Re-loading the same JSON twice changes nothing on the second pass.
- Loading a run over a database with a human-reviewed row leaves that row's
  `status`, `reviewer`, `reviewed_at` and `review_note` byte-identical.
- `probate.v_estate_property` and `probate.v_review_queue` return what the run
  report said they should.
