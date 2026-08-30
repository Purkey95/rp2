# MonitorCLT Signal Taxonomy

The single, prioritizable source of truth for signals — built to answer the one
question that matters once you have 150+ ideas: **which 25–40 do we build first?**

`signal_taxonomy.json` consolidates the signals scattered across the score
weights, the sourcing registry, and the env sources into one list, each carrying:

- `family` (19 families) and `dimension` (the five-score bucket)
- `source` and `availability` — wired / derived / env-free / free-add / sos-paid /
  rod-browser / court-browser / mls-paid / not-obtainable
- `cadence`, `legality` (ok / sensitive), and the scoring attributes
  `value` / `reliability` / `effort` (1–5)
- `status` — built / config-ready / not-started

## Prioritize

```bash
python3 prioritize.py          # build-next order, quick wins, gated, coverage
python3 test_prioritize.py
```

`priority = value × reliability × accessibility ÷ effort`, where accessibility
comes from availability — so "high value but can't get it cheaply" is correctly
*not* build-next. The output has four parts:

1. **Build next** — the ranked pending signals.
2. **Quick wins** — free/derived data, high value, low effort. Build these.
3. **High value, gated** — worth it, but blocked on a credential (SoS), a paid
   feed (MLS), or browser-only public records (ROD/courts). Pursue deliberately,
   not first.
4. **Coverage by family** — where the gaps are.

## Why this over more lists

Every prior session added signal *ideas*; this stops the sprawl and turns them
into a plan. The prioritizer's verdict is consistent and actionable: the next
builds are the **free/derived** signals we can compute or fetch today
(multi-year tax staging, expired/stalled permits, 311 complaint velocity,
portfolio-liquidation, hidden-density, neighborhood contagion, owner-network),
not the high-value-but-gated ones (mechanic's liens, probate, MLS) that need a
data deal first.

The taxonomy is a **seed** (~50 signals across the 19 families); it grows toward
150–250, but the value is the ordering, not the count. It also carries the
`signal_confidence_model` entry — the architectural next step (§ below).

## Next architectural step: signal confidence

The taxonomy flags `signal_confidence_model` as high priority. Today a signal
carries `source_url` + `bucket`; the confidence model makes **reliability** a
first-class scoring input, so a county-verified open code case outweighs an
AI-inferred vacancy. Each signal would carry severity / recency / reliability /
uniqueness, and the score would discount low-reliability inputs — preventing
garbage data from producing garbage leads. The `reliability` column here is the
seed of that model.
