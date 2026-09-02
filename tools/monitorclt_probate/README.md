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
| **Business entity** (NC Secretary of State, optional) | SOS id | entity name, status, addresses, company officials, registered agent — the only way a parcel owned by the decedent's LLC becomes reachable |

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

6. **Capped paths.** Two kinds of parcel never enter the pipeline above and
   are reached separately, and **neither can ever be auto-confirmed**
   (`crossref.CAP_AT_PENDING` — a code constant, not a rule, so calibration
   cannot tune it away):
   - **Held through an entity.** The owner string names an LLC or corporation;
     `--entities` supplies NC SOS records naming its officials. A decedent who
     is an official of the entity on title links with `entity_official_link`
     (`+0.10`), a registered agent with `registered_agent_link` (`−0.10` — it is
     usually a law office), the estate's mailing address at the entity's
     principal office `+0.15`, suffix disagreement (INC on the parcel, LLC at the
     SOS) `−0.15`, and an entity name that resolves to more than one SOS id
     `−0.15` (ambiguity is reported, never guessed at). Entity names are matched
     exactly after stripping legal-form noise; nothing fuzzy. Flag
     `held_via_entity`.
   - **Held by a named trustee.** `PUBLIC JOHN Q TRUSTEE` is a person in a role,
     so the name enters blocking and is scored normally. Flag `held_in_trust`.
     `PUBLIC FAMILY TRUST` — no person named — stays an organization and stays
     unreachable; trusts are not registered anywhere this tool reads.

   A capped link is an interest in the entity or the trust, not the parcel.
   The review floor still rejects the weak ones; the strong ones sit in
   `pending` for a human, and the schema's `CHECK` refuses a confirmed row that
   carries either flag without `reviewed_at`.

`post_death_conveyance` is flagged when a deed from the decedent is recorded
*after* the date of death: identity corroborated, but the parcel may already have
left the estate. Parcels whose owner reads `ESTATE OF …` with no matching estate
case are reported separately as an **input gap** — usually a county or date range
you have not pulled.

## Run

```bash
python3 crossref.py --estates  sample/estate_cases.jsonl \
                    --parcels  sample/parcels.jsonl \
                    --deeds    sample/deeds.jsonl \
                    --entities sample/business_entities.jsonl \
                    --json out.json
python3 test_crossref.py
```

Each row of `matches` in the JSON maps 1:1 onto `probate.entity_match`;
`probate.v_estate_property` is the lead list (confirmed only) and
`probate.v_review_queue` is what a human still has to clear. The sample data is
synthetic.

**Getting records in.** Nothing here fetches anything: county sources have terms,
the estates portal forbids automated access outright, and the SOS bulk data is a
subscription (see `agents/sources.md`). `intake/adapt.py` turns a vendor CSV into
the JSONL above through a map file — the only place a rename happens — and
refuses, with reasons and a non-zero exit, rather than guess at a field:

```bash
python3 intake/adapt.py --input export.csv --map intake/maps/estates.json \
        --out estate_cases.jsonl --county MECKLENBURG --source-url-base https://...
python3 intake/adapt.py --input corps.csv --map intake/maps/ncsos.json \
        --child officials=officials.csv --out business_entities.jsonl --source-url-base https://...
```

The maps in `intake/maps/` were written against the fixtures in `sample/intake/`
and are marked `TO VERIFY` against each vendor's real layout.

**Loading a run.** `load_run.py` writes one transaction of SQL for `psql`. It is
not quite the "straight insert" it looks like: a confirmed row must name a
reviewer (it gets the rule run, `rules@1.1/run:N`, never an invented person),
and because `entity_match` is unique per pair rather than per run, a re-run
must not overwrite a human's decision — reviewed rows take fresh evidence and
keep their status. The load ends by listing every pair where the matcher now
disagrees with a person.

```bash
python3 load_run.py --run out.json --estates ... --parcels ... --deeds ... --entities ... > load.sql
psql "$DSN" -v ON_ERROR_STOP=1 -1 -f load.sql
```

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

Two more things it does not do, by construction rather than by policy. A
`held_via_entity` or `held_in_trust` match is not a finding that the estate
holds the parcel — the entity or the trust does, and what interest the estate
has in *that* is for the estate attorney. And a trust with no named trustee on
the owner string is not reachable at all: there is no registry to resolve it
against.

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

On the synthetic sample it reports 100% precision and 37.5% recall at
auto-confirm, with 100% recall through the review queue — the intended shape,
and a reminder that these numbers describe the sample, not Mecklenburg. Capped
pairs (entity- and trustee-held) are listed in their own section: they count as
misses at auto-confirm, because they never reach the lead list, but they are not
a weights problem and no threshold reaches them. Label the hard cases
(common surnames, remarriages, junior/senior pairs) or the harness flatters
itself.
