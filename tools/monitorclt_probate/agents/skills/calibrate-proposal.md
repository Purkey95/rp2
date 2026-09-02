---
name: calibrate-proposal
description: Run evaluate.py against the label set and propose match_rules.json changes for a human to apply. Use for the monthly calibration pass. Never applies a change.
---

# calibrate-proposal

Run `evaluate.py`, forward what it reports, and write a proposal.

`../../README.md`'s "Calibrate before trusting the numbers" explains the invocation
and how to read every section of the report — the two operating points, blocked
out versus scored low, precision per tier and per evidence label, the sweep and
its two picks. Read it there. This skill covers only what happens afterwards,
which is written down nowhere.

```bash
python3 evaluate.py --estates <in>/estate_cases.jsonl \
                    --parcels <in>/parcels.jsonl \
                    --deeds   <in>/deeds.jsonl \
                    --labels  <labels>.csv \
                    --target-precision 0.95 \
                    --json runs/eval-<date>.json
```

## Forward, do not re-derive

Copy the report. Do not recompute a precision, restate a recommendation in your
own words, or characterise a number as good. `evaluate.py` already picks the
lowest `auto_confirm` that clears target precision, and already explains why
that is usually the one to ship in a recall-then-review design where a wrong
`confirmed` costs far more than a `pending`.

## Never apply a change

Write a proposal. A human edits `../../match_rules.json`.

A matcher whose weights an agent changed is a matcher nobody can defend, and
`../../match_rules.json` is the entire policy of the system — thresholds, evidence
weights, `require_corroboration_to_confirm`, name formats. The file is read-only
to every bot.

## The proposal

1. **The exact diff.** Which keys, from what to what. One coherent change per
   proposal — a diff touching four weights cannot be evaluated.
2. **What the report says it buys.** Precision and recall at both operating
   points, before and after, from `evaluate.py`. Not your estimate.
3. **What it costs.** Which currently-confirmed pairs would stop confirming;
   which currently-rejected would become pending.
4. **The label evidence.** How many labels support this, and how many of them
   are hard cases — common surnames, remarriages, junior/senior pairs. A
   proposal resting on easy labels is resting on nothing.
5. **The recommendation to hold**, when that is the answer. Most months it is.

## The gate

A proposal is adoptable only if **the label set has grown since the last change**.

Re-tuning against a static label set is tuning to the test: precision goes up on
the file and nowhere else. If the labels have not grown, the proposal is "label
these pairs first", and that is a complete and correct output.

`evaluate.py` emits `unresolved_labels` — labelled pairs it could not resolve
against the records. Report those every time; they are usually a county or PIN
problem, and a PIN that exists in two counties is an error the harness refuses
to guess at.

## The list nothing else reads

`skipped_estates`, from the run report: decedent names `crossref.py` could not
parse into first + last. These never enter the pipeline at all — they are not
low-scoring, they are absent, and no threshold change reaches them. They are a
`name_formats`, `suffixes` or `organization_tokens` problem, and they are
Calibrator's, because nobody else looks at them.

Distinguish the same way `evaluate.py` does for misses: **blocked out** is a
parsing and blocking problem, **scored low** is a weights problem. Proposing a
threshold change for a blocked-out miss cannot work.

## Verify

Re-run `evaluate.py` with the proposed values before proposing them, and quote
that run. A proposal whose effect you have not measured is a guess with a table
around it.
