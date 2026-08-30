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

Two cases the inheritance rule turns on, because they read the same but are not:
in a `LAST FIRST` source `PUBLIC JOHN Q & MARY B` is Mary B Public (a forename
and an initial) while `SMITH JOHN & JONES MARY` is Mary Jones — a second surname,
not an inherited one. A fragment carrying an estate marker still inherits:
`SMITH JOHN & MARY HEIRS` is Mary Smith, and she is the party this tool exists to
find. A person named on an organization-style owner string
(`PUBLIC JOHN Q TRUSTEE`) is a candidate too, carrying the `−0.40` contradiction
and barred from auto-confirmation — visible for review rather than dropped.

`post_death_conveyance` is flagged when a deed from the decedent is recorded
*after* the date of death: identity corroborated, but the parcel may already have
left the estate. Parcels whose owner reads `ESTATE OF …` with no matching estate
case are reported separately as an **input gap** — usually a county or date range
you have not pulled.

## Run

```bash
python3 crossref.py --estates sample/estate_cases.jsonl \
                    --parcels sample/parcels.jsonl \
                    --deeds   sample/deeds.jsonl \
                    --json out.json
python3 test_crossref.py
```

Each row of `matches` in the JSON maps 1:1 onto `probate.entity_match`, so loading
a run is a straight insert; `probate.v_estate_property` is the lead list
(confirmed only) and `probate.v_review_queue` is what a human still has to clear.
The sample data is synthetic.

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
