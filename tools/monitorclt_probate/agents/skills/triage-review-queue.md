---
name: triage-review-queue
description: Turn probate.v_review_queue rows into a packet of questions a reviewer can answer, and escalate rows that have waited too long. Use for the daily review pass.
---

# triage-review-queue

Read `probate.v_review_queue` and produce a packet a human can work through.

The view already does the ordering: pending rows, score descending, with
`evidence` and `flags` attached. Read the view, not the run JSON — the view
reflects decisions reviewers have made since the run, and the JSON does not.

## The rule this skill exists to protect

**The packet asks. It never answers.**

Every `pending` row is there because `crossref.py` deliberately declined to
decide. Re-ranking by how promising a row looks, grouping into "likely" and
"unlikely", leading with the ones you would confirm, or writing "this one seems
clear" all move the disposition into a model. That is the single failure this
whole design exists to prevent, and it is easy to do by accident while being
helpful.

Write in questions. Never write that a match *is*.

## Per row

Render, in this order:

1. **The pair.** `left_id` and `right_id` — county/file number and county/PIN —
   plus decedent name, owner string as printed, and situs address. Enough to
   find both records.
2. **The flags, first, before anything else.**
   - `name_only_needs_human_review` — nothing corroborates this beyond a shared
     name, and `../../match_rules.json` sets `require_corroboration_to_confirm`, so
     it can never be confirmed by score alone however high the score is. Say
     that plainly.
   - `post_death_conveyance` — a deed from the decedent recorded *after* the
     date of death. Identity is corroborated; the parcel may already have left
     the estate. This is a different question from the identity question and
     must not be blurred into it.
   - `held_via_entity` — the parcel is owned by an LLC or corporation, and the
     decedent was one of its officials (or, weaker, its registered agent; the
     `via:` line says which). The entity holds title. The estate holds, at
     most, an interest in the entity. **Never write "the estate holds this
     parcel"** for such a row; write "the decedent's LLC holds this parcel".
     The matcher will not confirm it however it scores, and neither may this
     packet suggest it.
   - `held_in_trust` — the owner string names the decedent as trustee. The
     trust holds title; same rule.
3. **The evidence list in English.** Translate the labels in
   `../../match_rules.json` — `mailing_address_match` becomes "the estate's mailing
   address on file matches the parcel's tax-mailing address". Do not restate the
   weights or recompute the score; the score is a number the matcher produced,
   shown as-is.
4. **The specific check.** What would settle this row, named concretely:
   - `name_only_needs_human_review` → is there any record connecting this person
     to this parcel other than the name?
   - middle-initial conflict in the evidence → are these one person or two?
   - `post_death_conveyance` → was this parcel conveyed out of the estate, and
     when?
   - a common-name penalty → how many people share this name in this county?
   - `held_via_entity` / `held_in_trust` → does the estate hold an interest in
     this entity or trust, and what is it? A question for the estate attorney,
     not a records check; say so in the packet.
5. **Where to look.** The `source_url` of both records.

## Rows that are not about identity

Two lists in every run report are not review rows and must be routed elsewhere,
not folded into the packet:

- `unmatched_estate_parcels` — an intake coverage gap. Goes to Intake.
- `skipped_estates` — decedent names `crossref.py` could not parse into first +
  last. A name-parsing problem that no reviewer can fix and no threshold can
  reach. Goes to Calibrator.

## Age

Escalate any row whose `created_at` is older than N days (start at 14) to Clerk,
once, and then stop. Repeating an identical nag daily trains people to stop
reading packets, which costs more than the stale row does.

If the queue is empty, send nothing. Silence is the correct output.

## Verify

Before sending: does any sentence in the packet read as a recommendation? Would
a reader who skimmed only the first line of each row come away with an
impression of which are real? If yes, rewrite. Confirm the counts match the view
and every row carries both `source_url`s.

`../../README.md` step 3 stands regardless of how a row is resolved: `confirmed` is
a records match, not a conclusion, and title, liens, heirs and the
representative's authority are still checked before anyone is contacted.
