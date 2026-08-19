# MonitorCLT Market Timing

The **when/where** layer. Pulls free FRED series (no API key), computes each
indicator's level, 6/12-month change, direction, and **acceleration** (the second
derivative), plus derived indicators (mortgage spread, jobs-to-permits ratio),
and prints a decision-oriented read grouped by leading / coincident / lagging.

Kept deliberately **separate from the Owner Distress Score**: market indicators
say *when/where* to lean in; parcel signals say *what/who* to buy. The output
here is meant to **modulate** distress leads (which submarket, how aggressive) —
never mixed into a parcel's score. Lagging series (price indices) are carried as
confirmation only, never as signals.

## Run

```bash
python3 market.py                    # live from FRED (curl; fast on the host)
python3 market.py --offline sample   # from cached <id>.csv (deterministic/CI)
python3 test_market.py               # analysis logic, offline
```

Writes `out/market_signals.json` (structured indicators + derived + synthesis).

## Series

`series.json` lists the (verified, keyless) FRED series with their layer, geo,
and `rising_read` (what an upward move means for a distressed-acquisition buyer).
Charlotte metro uses CBSA 16740 / BLS metro code 737 — swap those ids for another
metro. Adding a series is one entry; the analysis and synthesis pick it up.

## Derived

- **mortgage_spread** = `MORTGAGE30US − DGS10` — lender risk appetite; widening =
  credit tightening / risk-off (also drives hard-money pricing).
- **jobs_to_permits** = 12-mo metro job growth ÷ 12-mo metro permits — ~1.2–1.5
  balanced, <1.0 local oversupply, >1.5 demand outrunning building.

## Scope

This module wires the **free, API-accessible, consistently-updating** subset of
the market-indicator universe. What's gatherable where (both indicator batches),
which model each feeds, and what's paid / public-records-browser / not-obtainable
is laid out in `ASSESSMENT.md`. The guiding rule (the founder's own): a handful
measured monthly at a consistent geography beats forty measured once.

## Environment note

FRED responds to `curl --http1.1` (its HTTP/2 endpoint stalls through this
sandbox's proxy under urllib); on the MonitorCLT host it's direct and fast. The
analysis is unit-tested offline so it never depends on network reachability.
