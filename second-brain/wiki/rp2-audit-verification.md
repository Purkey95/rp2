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

## Confirmed and fixed (second pass)

### Country accounting-method restrictions were half-enforced

A country declares which accounting methods are legal, but only one of the two
paths that select one checked it. Demonstrated with Japan, which permits `fifo`
alone:

```
rp2_jp -m lifo ...                 -> rejected: invalid choice: 'lifo'
[accounting_methods] 2020 = lifo   -> accepted: "Accounting method: lifo"
```

`-m` is constrained by argparse choices built from
`country.get_accounting_methods()`; the configuration section was checked only
for module existence. A report could be computed with a method illegal in the
filer's jurisdiction. Both paths now enforce the same set.

### The `generators` option never worked — two stacked defects

`docs/input_files.md` and the JSON schema both define `generators` as a **key**
of the `general` section, but the guard tested whether a `[generators]`
**section** existed. The documented spelling was therefore silently ignored and
the default set always used. Past that guard, configured names were stored bare
while plugins are matched by fully qualified module name, so the run would have
aborted with `Report generator plugins ... not found`.

Both fixed. `generators = open_positions` now emits only that report where it
previously emitted all three; a country-specific name (`us.tax_report_us`)
works; omitting the key still yields the full default set.

## Refuted

**"Dead CodeQL scanning."** The workflow triggers on pushes to `main`, PRs to
`main`, and a weekly cron, and a CodeQL run completed with conclusion `success`
on 2026-08-28. It is not dead. It *is* pinned to `github/codeql-action@v1`,
which GitHub has retired — worth updating, but a different and much smaller
problem than the audit describes.

## Still not checked

Transaction-type localization and the packaging-metadata claims.

**Note on provenance:** `AUDIT.md` states it was requested for a repo called
"monitorclt" which does not exist on GitHub, and covers `rp2` instead. See
[[monitorclt]].
