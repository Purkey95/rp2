# MonitorCLT Probate → Real Property Cross-Reference

**Can you cross-reference these databases against real estate holdings in probate
matters?** Yes — and this is the join that actually works: *estate case → parcel →
deed*. It starts from a probate filing (a decedent, a file number, a personal
representative on record with the Clerk of Superior Court) and answers whether that
estate appears to hold real property, and where.

It does not start from a person and ask whether they are in a probate matter, and it
takes **no incarceration, probation, parole, or arrest data as input at all** — see
*What this does not do*, below.

## What gets joined

| Source | Keyed by | What it contributes |
|---|---|---|
| **Estate case** (Clerk of Superior Court, estates division) | county + file number | decedent, date of death, filing date, status, personal representative and mailing address |
| **Parcel / assessor** (county tax + GIS, public record) | county + PIN | owner string, situs and tax-mailing address, land use, assessed value, deed book/page |
| **Deed** (Register of Deeds) | book/page or instrument no. | grantor, grantee, recording date, instrument type, PIN as printed |

Sources are systems of record and are never mutated by matching. Every assertion
that two records are the same person lives in `entity_match` with its evidence,
and is reviewable and reversible. `schema.sql` is the PostgreSQL model.

## How the match is decided

Deterministic and staged, because **a shared name is not evidence**:

1. **Block.** A parcel is a candidate only if some party in its owner string shares
   the decedent's FIRST + LAST. Nothing weaker enters the pipeline.
2. **Name tier.** `name_full_exact` (middle name or initial agrees) `0.70`, or
   `name_first_last_only` `0.55`.
3. **Corroborate.** Estate marker on the owner string (`ESTATE OF`, `HEIRS`,
   `LIFE ESTATE`) `+0.18` · estate's mailing address on the parcel `+0.15` · a
   recorded deed naming the decedent as grantor of that PIN `+0.15` · situs match
   `+0.10`.
4. **Contradict.** Middle-initial conflict `−0.30` · organization owner `−0.40` ·
   wrong county `−0.50` · a forename+surname common enough in the parcel index
   (≥ 8 parcels) `−0.15`.
5. **Dispose.** `≥ 0.85` **and** at least one corroborating item **and** same
   county **and** not an organization → `confirmed`; `≥ 0.45` → `pending` (human
   review); below → `rejected`. **A name-only match can never be confirmed,
   however high it scores.**

Name parsing handles what county data actually looks like: assessor rows in
`LAST FIRST MIDDLE` order, court rows in `FIRST MIDDLE LAST`, comma forms,
suffixes, `PUBLIC JOHN Q & JANE R` (the second owner inherits the printed
surname), organizations, and `ESTATE OF …` prefixes (which flip the remainder
back to natural order). All tunable in `match_rules.json` — no code change.

`post_death_conveyance` is flagged when a deed from the decedent is recorded
*after* the date of death: identity corroborated, but the parcel may already have
left the estate. The flag is all a single run can say; the store (below) turns it
into a `transferred` status and takes the parcel off the lead list. Parcels whose owner reads `ESTATE OF …` with no matching estate
case are reported separately as an **input gap** — usually a county or date range
you have not pulled.

## Run

```bash
python3 crossref.py --estates sample/estate_cases.jsonl \
                    --parcels sample/parcels.jsonl \
                    --deeds   sample/deeds.jsonl \
                    --json out.json
python3 test_crossref.py   # likewise test_store.py, test_transfers.py, test_backtest.py, test_evaluate.py
```

Each row of `matches` in the JSON maps 1:1 onto `probate.entity_match`, so loading
a run is a straight insert; `probate.v_estate_property` is the lead list
(confirmed only) and `probate.v_review_queue` is what a human still has to clear.
The sample data is synthetic; `sample/pull_2026-09/` is the same county
pulled again five months later (one estate sold, one distributed to its
executrix, one new filing), for the loader and the backtest.

## Keep a history: the loader

`crossref.py` on its own is stateless -- it cannot tell you what is new today, and
it cannot notice that a parcel it confirmed last month has since been conveyed.
`load_run.py` fixes both. Run it once per pull, dated by the pull:

```bash
python3 load_run.py --db monitorclt.sqlite --as-of 2026-09-01 \
        --estates pull/estate_cases.jsonl --parcels pull/parcels.jsonl --deeds pull/deeds.jsonl
```

It runs the cross-reference, writes the run into a SQLite store (`store.py`;
`schema.sql` carries the same tables for Postgres), and prints the delta against
the previous run: new confirmed, new pending, status changes, and every parcel
detected leaving its estate. `--as-of` is the date the inputs describe, so a
backfill of dated historical pulls builds correct history.

`report.py` reads it back:

```bash
python3 report.py --db monitorclt.sqlite daily                   # new targets per pull date
python3 report.py --db monitorclt.sqlite daily --by filing_date  # ...per estate filing date
python3 report.py --db monitorclt.sqlite leads                   # confirmed and still in the estate
python3 report.py --db monitorclt.sqlite transfers               # everything that has left an estate
python3 report.py --db monitorclt.sqlite outcomes                # how the model's calls held up
```

`daily` counts pairs by the pull that first surfaced them (what arrived today) and
by the pull that saw them leave; `--by filing_date` is the way to look back before
the store existed, since one historical pull yields the cohorts by when each
estate was opened. History starts with the first pull loaded -- there is no
other source for "what the matcher would have said on a day nobody ran it".

## Transfers: a sold parcel is not a lead

After every load the store re-reads, for each tracked (confirmed or pending) pair,
every deed on that parcel and the assessor row it snapshotted last time. The
readings live in `transfers.py` and are deliberately narrow:

| Event | What was seen | Effect on the match |
|---|---|---|
| `deed_from_estate` | post-death deed whose grantor is the personal representative, or the decedent's name with an estate marker, or the decedent's name on a fiduciary instrument | **`transferred`** -- off the lead list |
| `owner_changed` | assessor owner string no longer carries the decedent | **`transferred`** |
| `owner_restyled` | owner string changed but still names the decedent (`SMITH DAVID` -> `SMITH DAVID ESTATE OF`) | none; recorded |
| `sale_date_advanced` | `last_sale_date` moved past the date of death | none; recorded |
| `namesake_conveyance` | plain warranty deed in the decedent's bare name, recorded after death -- a dead person does not sign one, so that owner was somebody else | back to **`pending`** |
| `ambiguous_conveyance` | decedent-name grantor whose middle initial contradicts the estate | none; recorded |

`transferred` is sticky: the assessor lags the Register of Deeds by weeks, and a
rule run re-confirming the pair does not put it back. Each event records the
instrument (or the two snapshots) that showed it, and where the parcel went --
to the representative, within the family name, to an organization, or to a
third party -- read from `grantee_name`, which the matcher itself never uses.

The `outcomes` report is the same events turned around: a `deed_from_estate` on a
pair the model confirmed is a true positive it earned, a `namesake_conveyance` is
a false positive it made. Tallied per tier and per evidence label, it is the
live version of `evaluate.py`'s precision table, built from real conveyances
instead of hand labels.

## Backtest: what the record later proved

`backtest.py` runs the matcher as of a past date and scores it against deeds
recorded since. Estates filed by the cut-off are the cases; deeds recorded by
then are the evidence the matcher may see; deeds recorded after it are the
answer key it never saw. An executor's or administrator's deed, or a deed from
`ESTATE OF <decedent>`, proves the estate held that parcel (positive); a plain
warranty deed in the decedent's bare name after death proves a namesake owned
it (negative). The labels then go through `evaluate.py` unchanged.

```bash
python3 backtest.py --estates pull/estate_cases.jsonl --parcels pull/parcels.jsonl \
                    --deeds pull/deeds.jsonl --start 2026-01-01 \
                    --as-of 2026-03-31 --as-of 2026-06-30 --labels-out backtest_labels.csv
```

**The catch.** The parcel index you can pull today shows the *buyer*. A parcel
that sold out of an estate no longer carries the decedent's name, so blocking
fails and exactly the cases you want to learn from vanish -- run with
`--rollback none` to see how much. For every parcel with a later deed the
backtest reconstructs the cut-off owner from the deed chain (the grantee of the
last deed before the cut-off; failing that, the grantor of the first deed
after), blanks the fields a cut-off assessor row could not have had, and reports
how many it reconstructed and how. A parcel that could only be reconstructed
from a fiduciary grantor -- no earlier deed in the pull -- can only be missed,
and is reported as the deed chain falling short, not the matcher. Pull deeds
further back than the estates. A snapshot the store actually took at the time
is always better than a reconstruction; the backtest is for the months before
there was a store.

Two more honest limits: positives exist only for estates that *conveyed*
something after the cut-off, so recall is recall on the subset that later sold
(an estate still holding its house produces no label); and negatives come only
from the namesake reading, so a pair nothing ever contradicts stays unlabeled.
The report says both, every time.

On the sample (`sample/pull_2026-09/`, the September pull) as of 2026-04-15 it
finds 3 positives and 1 negative: one estate the matcher had in review, one it
auto-confirms only after the assessor's later `HEIRS` retitle, and one it can
never block on because the 2018 deed indexed the buyer as `EXAMPLE A B`. That
last one is the kind of miss worth knowing about, and the reason `--labels-out`
exists: merge the derived labels with hand labels and let the threshold sweep
and the per-evidence table say what to change in `match_rules.json`. Bump
`version` when you do; every run records it, so the store shows the effect.

That is the correction loop, and it is a human one on purpose. The model is
twelve weights and two thresholds; letting deed-derived labels retune them
automatically would overfit the handful of estates that happen to have sold,
and `deed_grantor_link` is both a matcher input and the label source -- only the
strict cut-off keeps those apart.

## The workflow this belongs to

1. Pull **newly opened estate cases** by county — record the *personal
   representative*, not just the decedent.
2. Cross-reference to parcels and deeds (this tool) → estates that appear to hold
   real property.
3. **Verify before acting**: chain of title, liens, heirs, whether the property is
   actually in the estate, and the representative's authority. `confirmed` is a
   records match, not a conclusion.
4. Contact the **personal representative or estate attorney** — the authorized
   decision-maker — and nobody else.
5. Load every pull (`load_run.py`) so tomorrow has a yesterday: new targets are
   counted, and a lead that has since sold is retired before anyone calls about it.

## What this does not do

There is no incarceration, probation, parole, or arrest table in the schema and no
such field in the matcher, by design. Custody status is not a lead source, not a
lead score, not a distress label, and not a segmentation field. It does not imply
incapacity, financial distress, lack of representation, ownership, or willingness
to sell, and NC DAC's public database covers state prison/probation/parole — not
county jails — so it is an incomplete signal even on its own terms. Criminal-history
data also carries fair-housing exposure (HUD has warned that criminal-record-based
housing restrictions can produce unjustified disparate impacts under the FHA).

If a narrow, documented need ever arises — e.g. an heir or representative may need
counsel or alternative communication — treat it as restricted manual research
outside this pipeline: access-controlled, logged per lookup, short retention, and
never merged into CRM, marketing lists, ad-platform uploads, or automated outreach.

Two more guardrails that live outside the code: run the workflow past a North
Carolina real-estate/probate attorney before operationalizing it, and keep
solicitation of estates within NC rules on contacting personal representatives.

## Calibrate before trusting the numbers

The weights in `match_rules.json` are defensible starting points, not measured
ones. `evaluate.py` is how you find out what they are actually worth: label
estate/parcel pairs by hand, then score the matcher against them.

```bash
python3 evaluate.py --estates sample/estate_cases.jsonl \
                    --parcels sample/parcels.jsonl \
                    --deeds   sample/deeds.jsonl \
                    --labels  sample/labels.csv \
                    --target-precision 0.95
```

Labels are a CSV with a header — `file_number, pin, is_match` (1/0); counties are
resolved from the records, and a PIN that exists in two counties is an error
rather than a guess. The report gives:

- **two operating points** — auto-confirm (what reaches outreach with no human;
  precision is what matters) and through-review (confirmed + pending: the recall
  ceiling a reviewer could ever reach)
- **why each match was missed** — *blocked out* (never became a candidate: a
  parsing/blocking problem no threshold fixes) vs *scored low* (a weights problem)
- **precision per tier and per evidence label** — which corroboration keeps its
  promises and which is just adding points
- **a threshold sweep** with two picks: best F1, and the lowest `auto_confirm`
  that still clears your target precision — usually the one to ship, since this
  is a *recall-then-review* design where a wrong `confirmed` costs far more than
  a `pending`

On the synthetic sample it reports 100% precision and 50% recall at auto-confirm,
with 100% recall through the review queue — the intended shape, and a reminder
that these numbers describe the sample, not Mecklenburg. Label the hard cases
(common surnames, remarriages, junior/senior pairs) or the harness flatters
itself.
