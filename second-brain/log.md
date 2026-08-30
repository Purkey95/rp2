# Operations Log

Append-only timeline of ingests, queries, and lint passes.
Entry prefix format: `## [YYYY-MM-DD] operation | description`
(`grep "^## \[" log.md | tail -5` shows the last 5 operations.)

## [2026-07-08] init | Vault created

Scaffolded from Karpathy's LLM Wiki pattern: schema (CLAUDE.md), empty wiki
with index, immutable raw/ sources directory, and this log.

## [2026-08-29] query | Assessment of the MonitorCLT life-event property intelligence proposal

Design proposal received in conversation (not filed in `raw/`) and assessed;
answer filed as a 13-page cluster. Verdict: adopt the event-extraction
architecture, invert the build order (property-keyed foreclosure before
person-keyed obituaries), and withhold any numeric score until backtested.
Recorded seven unverified factual claims from the source on
`life-event-property-intelligence-engine`; the NC eCourts portal access
terms are flagged as the blocking open question.

## [2026-08-29] build | Parcel enrichment layer (MonitorCLT Step 1)

Built `monitorclt/` against the live Mecklenburg CAMA ArcGIS service: address
and owner normalization, SQLite index with provenance and `observed_at`, and
person-to-parcel candidate generation that emits evidence rather than a
confidence score. Full county load verified at 428,504 parcels in ~4 minutes,
reconciling exactly with the service's own record and distinct-`pid` counts.

Filed [[mecklenburg-cama-parcel-data]] with five traps the live data exposed
(non-unique `pid`, unreliable name split, substring false positives, `UNINC`,
non-USPS suffixes) plus current signal populations. Updated [[monitorclt]]
(buy-vs-build resolved: build), [[mecklenburg-foreclosure-slice]] (Step 1
done), [[entity-resolution-for-property-records]] (two decedent name orders)
and [[public-record-source-matrix-mecklenburg]] (CAMA row verified).

## [2026-08-29] build | Backtest harness, and the first negative result

Loaded 1,492,220 Mecklenburg sales (1980-2026) and built a backtest that
reconstructs owner state from the sales chain alone, avoiding the CAMA feature
leak. Ran four as-of dates.

Result: long tenure anti-predicts sales at ~0.45x lift, stable across 2012,
2016, 2019 and 2022 — the opposite sign to the proposed scoring ladder. The
control signal (recent buyers) outperforms every tenure signal. The estate
outcome label proved unusable: the grantor field records only 13-33 estate
sales a year countywide, so the estate hypothesis cannot be tested with this
data at all. A forced-sale result visible at 2022 alone did not replicate and
is recorded as noise. Filed as [[backtest-results-2026-08]].

## [2026-08-30] build | Propensity model: works, ranks the wrong population

Fitted a transparent cell model over (owner_type, tenure_bucket), trained
2016-18 and validated out of time on 2022-24. Top decile reaches 2.53x lift
and captures 25% of arms-length sales — a real result. But deciles 2-7 are
flat at ~1.0x, absolute probabilities do not transfer across market regimes,
and the top decile is COMPANY/0-3yrs: investor inventory being flipped, the
worst possible leads for the intended business. Filed as
[[propensity-scoring-findings]].

Caught and fixed a label leak mid-analysis: sorting rows by (score, outcome)
ordered positives first within tied blocks and faked a 2.56x top decile with
0.00% middle deciles. Regression test added.
