# Skills

Five recipes, one per stage of the pipeline that has no implementation today.

| Skill | Stage | Bot |
|---|---|---|
| [`intake-records`](intake-records.md) | Acquire and normalize county records | Intake |
| [`load-run`](load-run.md) | Run JSON → `probate.*`, idempotently | Runner |
| [`triage-review-queue`](triage-review-queue.md) | `v_review_queue` → questions for a reviewer | Queue |
| [`calibrate-proposal`](calibrate-proposal.md) | `evaluate.py` → a proposed diff | Calibrator |
| [`draft-pr-outreach`](draft-pr-outreach.md) | `v_estate_property` → a draft, never a send | Counsel-draft |

There is deliberately no skill for running `crossref.py` (one command line,
already in `../../README.md`) and none for the weekly report (`format_report()`
already prints it — see [`../README.md`](../README.md) on what this layer does
not duplicate).

Every skill assumes [`../AGENTS.md`](../AGENTS.md) is loaded.
