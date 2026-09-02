---
name: draft-pr-outreach
description: Draft correspondence to a personal representative or estate attorney from a verified confirmed lead, for a human to review and send. Human-initiated only; never sends.
---

# draft-pr-outreach

Draft one letter. A human reviews it and a human sends it.

**Do not build or run this before the gate.** `../../README.md` requires the
workflow to be reviewed by a North Carolina real-estate/probate attorney before
it is operationalized, and solicitation of estates to stay within NC rules on
contacting personal representatives. That review covers this template and this
process. Until it exists, this file is a specification and nothing more.

## Three preconditions, all required

1. **The row is `confirmed`** — read from `probate.v_estate_property`, which is
   confirmed-only by construction. A `pending` candidate is a question, not a
   lead; the view exists precisely to keep it away from outreach tooling. Never
   query `entity_match` directly here.
2. **A human has completed step 3.** `../../README.md`: verify chain of title,
   liens, heirs, whether the property is actually in the estate, and the
   representative's authority. `confirmed` is a records match, not a conclusion,
   and this skill has no way to establish any of those things. The verification
   must be recorded and referenced before drafting begins.
3. **A human asked for this specific draft.** There is no routine, in
   [`../routines.md`](../routines.md) or anywhere else, and there will not be. A
   cadence that drafted outreach would be skipping step 3 by construction.

A row that is machine-confirmed and never reviewed —
`reviewed_at IS NULL AND reviewer LIKE 'rules@%'`, per
[`load-run`](load-run.md) — has not satisfied precondition 2.

## Addressee

The **personal representative or the estate attorney**, and nobody else.
`../../README.md` step 4, and the paragraph `crossref.py` prints at the foot of
every run. Not heirs, not neighbours, not occupants, not anyone whose name
appeared on a deed. The representative is the authorized decision-maker; that is
the entire reason the pipeline carries `personal_rep_name` and
`pr_mailing_address` from the estate case through to the rollup.

Address them in their capacity as representative of the estate.

## The letter

Say who you are, that the estate appears in the public record to hold a
particular parcel, where that record came from, and what you are asking. Keep it
short.

**Do not write:**

- that the estate is distressed, in difficulty, or needs to sell
- that the representative should act quickly, or any urgency framing
- anything about the decedent beyond what the estate file states
- a match score, an evidence label, or any suggestion the property was
  identified by an automated system with a confidence level
- an inference about the representative's or the heirs' circumstances,
  situation, or capacity
- anything drawn from a source outside the three record types in
  `../../schema.sql`. Read "What this does not do" in `../../README.md` before
  drafting; the excluded categories and the reasons are stated there, and this
  is the point in the pipeline where ignoring them would do the damage

The parcel was identified from public records. Say that plainly and let it be
unremarkable.

## Output

A draft, in a draft folder. Never a send.

Counsel-draft holds no send scope and no database write access — see the
least-privilege rule in [`../AGENTS.md`](../AGENTS.md). If a platform cannot
scope credentials that narrowly, do not connect mail to this bot at all.

Attach to each draft: the `left_id` and `right_id` it came from, the recorded
verification from precondition 2, and the reviewer who confirmed the match.
A draft nobody can trace back to a verified record should not be sent.

## Verify before handing over

Read the draft against the "do not write" list above, line by line. Then check:
is the addressee the representative or their attorney? Does the letter state
anything as fact that the three record types do not support? Would a reader
infer distress or urgency that nobody wrote?

If any answer is uncertain, hand the draft over with the uncertainty named
rather than resolved.
