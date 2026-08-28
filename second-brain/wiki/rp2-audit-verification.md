---
type: answer
created: 2026-08-28
updated: 2026-08-28
tags: [rp2, audit, correctness, tax]
---

# Verifying the rp2 Production-Readiness Audit

PR #3 on [[rp2]] adds `AUDIT.md`, which claims 10 critical issues. Four
correctness claims were checked against the code on 2026-08-28. **All four
describe something real**, but two are narrower than the audit states and one
is mischaracterized.

Related: [[outstanding-items-2026-08-28]] · [[rp2]]

## Confirmed and fixed

### US long-term capital gains misclassified — real, tax-consequential

`gain_loss.py` classified with a day count:

```python
(taxable_event.timestamp - acquired_lot.timestamp).days >= 365
```

IRS Topic 409 / Pub. 544: the holding period begins the day **after**
acquisition, includes the day of disposal, and the gain is long-term only if
held **more than** one year. A day count cannot express that anniversary:

| Acquired | Sold | Old | Correct |
|---|---|---|---|
| 2021-01-01 | 2022-01-01 | LONG (365 days) | SHORT |
| 2020-01-01 | 2021-01-01 | LONG (366 days, leap) | SHORT |
| 2020-02-29 | 2021-03-01 | LONG | SHORT |

Every error runs in the direction that **understates the tax owed**.

Fixed by adding an overridable `AbstractCountry.is_long_term_capital_gains()`
whose default is the existing day count — so every other country plugin,
including third-party ones, is unchanged — and overriding it for the US with
calendar-anniversary arithmetic. Seven regression tests added.

### JP report year ordering — real, one-line fix

`tax_report_jp.py` grouped transactions into a dict keyed in **encounter
order** across the chained in/out/intra sets, then iterated it directly. That
order is not chronological, while the sheets are laid out sequentially via
`previous_year_row_offset` and the summary carries totals forward — so a year
arriving out of order corrupted both layout and carried figures. Fixed by
iterating `sorted()`. The JP golden files were unaffected: their years already
happened to be encountered in order, which is why no test ever caught it.

## Confirmed but narrower than claimed

### ODS parser silently drops rows — real, bounded to one row per table

`ods_parser.py` treats the first row after a table-begin token as a header by
*trying* to parse it as a transaction: if parsing raises, it is assumed to be
a header and skipped. A genuine transaction in that position with one bad
field is therefore silently dropped.

This is real, but it is bounded to the **first data row of each table**, not
arbitrary rows, and the code carries an explicit upstream `TODO` acknowledging
it. Fixing it properly needs field-by-field heuristics to tell a malformed
header from malformed data — a design decision that belongs upstream, so it
was left alone rather than changed unilaterally in a fork.

## Confirmed mechanism, mischaracterized as security

### "Formula injection"

`abstract_ods_generator._fill_cell()` writes any string beginning with `=` as
a live spreadsheet **formula**. User-controlled strings (asset, exchange,
holder names) reach it.

The audit frames this as a security hole. RP2 is an offline single-user CLI
where the user supplies their own input file, so the "attacker" and the victim
are the same person — the threat model is weak. It is better understood as a
**data-integrity bug**: a holder or exchange legitimately named starting with
`=` is silently turned into a formula and renders as `#NAME?`. Worth fixing,
not worth alarm.

## Not yet checked

The audit's remaining claims — dead `generators` option, transaction-type
localization, config validation permitting country-illegal accounting methods,
dead CodeQL scanning, packaging metadata — were not verified in this pass.

**Note on provenance:** `AUDIT.md` states it was requested for a repo called
"monitorclt" which does not exist on GitHub, and covers `rp2` instead. See
[[monitorclt]].
