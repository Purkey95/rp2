# MonitorCLT Temporal Signal Engine

The **"why this property, why now?"** layer. The five-score model scores a
parcel's *current* signals; this scores the *shape over time* — because the same
four events mean very different things clustered in three months vs. spread over
eight years.

Reads a per-parcel event **timeline** and computes a `why_now` score from four
things the static score can't see:

- **recency** — recent events weigh more (decay by age)
- **velocity** — a cluster of events in a short window = escalation
- **sequence** — known escalation chains (code → failed inspection → demolition;
  renovation → mechanic's lien = stalled flip; tax delinquency → lien →
  foreclosure)
- **negative space** — an expected follow-up that never happened (fire but no
  repair permit; investor bought but no renovation; eviction but no re-listing)

## The point, in one run

Two parcels with the **identical three event types** — one clustered and recent,
one spread over seven years:

```
APN 07104521   why_now: 79.6   (3/3 recent, CLUSTER)   flags: code_to_demolition
  2026-05-01 code_violation  2026-06-15 failed_inspection  2026-07-20 demolition_permit

APN 11902388   why_now: 23.1   (1/3 recent)
  2019-03-01 code_violation  2022-06-15 failed_inspection  2026-07-20 demolition_permit
```

Same data points, 3.4× different lead. That difference is invisible to a
point-in-time list and is the whole reason to build a signal engine instead of
buying more lists.

## Run

```bash
python3 timeline.py --events sample/events.jsonl --today 2026-08-19 --outdir out
python3 test_timeline.py
```

Writes `out/why_now.jsonl` (per parcel: `why_now`, velocity cluster, matched
escalations + negative-space flags, and the dated narrative).

## How it fits

- **Input** = the same sourced signals, but **dated** (every adapter already
  captures `retrieved_at`; persist each signal's *event date* — permit issue,
  case open, recording date — into an `events` stream).
- **Severity** comes from the five-score `weights.json`, so a foreclosure event
  outweighs a permit.
- **Output** modulates the Seller Opportunity Score: the static score says *how*
  distressed / how strong the opportunity; `why_now` says *whether it's moving and
  whether now is the moment*. A high static score with a low `why_now` is a slow
  burn; a rising `why_now` with escalation flags is act-now.

Escalation chains and negative-space rules live in `rules.json` — add one per line
as new sources come online. Pure stdlib; `--today` keeps it deterministic.
