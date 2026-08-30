# Full Production-Readiness Audit — Purkey95/rp2

**Audit date:** 2026-07-09
**Scope:** entire repository (branch `claude/monitorclt-full-audit-7qf9y5`, at v1.7.1)
**Method:** file-by-file inspection of all source, tests, CI, packaging, and docs; suspicious behaviors were reproduced and verified empirically (runtime experiments on `RP2Decimal`, ConfigParser semantics, dateutil, ezodf, gettext binding, live CLI runs, and one full golden-file test run).

> **Scope note:** The audit request referenced a repository called "monitorclt". No repository by that name exists in this GitHub account (verified via repository listing). The only repository attached to this session — and the one this audit branch was created on — is **Purkey95/rp2**, a fork of eprbell/rp2 (a privacy-focused crypto tax calculator CLI written in Python). This audit therefore covers rp2 in full. Note also that rp2 is a **CLI tool with no web frontend, no database, and no authentication system** — audit areas such as "screens, forms, buttons, API routes, database tables" map here to: the CLI interface, the ODS spreadsheet input format, the `.ini` config format, generated ODS reports, error messages, and docs.

> **Fork status:** This fork sits at v1.7.1 while upstream eprbell/rp2 has released v1.7.2 (adds the LOST transaction type). All recent commits on this fork (LOFO accounting method, doc updates) are upstream commits by eprbell — the fork carries no original divergent code.

---

## A. Executive Summary

rp2 is a mature, well-architected crypto tax calculator with genuinely strong bones: strict typing (near-maximal mypy configuration), pervasive input validation, an immutable transaction data model, an O(n log n) AVL-backed lot-matching engine, a clean plugin architecture for countries/accounting methods/reports, and a discriminating golden-file test corpus spanning 5 countries and 4 accounting methods. The dynamic plugin loading was checked for arbitrary-import/injection paths and is safely confined to fixed package namespaces.

However, "inspect it like it ships tomorrow" turned up real problems in three bands:

1. **Wrong numbers in tax documents (worst band).** The Japanese tax report generates year sheets in dictionary-insertion order and assumes consecutive years, so gap years or out-of-order discovery produce `#REF!` errors or *silently wrong* moving-average carry-over figures. The US long-term capital gains test (`days >= 365`) misclassifies exact-one-year and leap-year-spanning sales — flipping the tax rate applied. The full report hardcodes "USD" on the Summary sheet even for JPY/EUR countries. The ODS parser's header-detection heuristic can silently drop a real transaction row.
2. **Documented features that don't work.** The `generators` config field is dead code (wrong ConfigParser membership test), config-file accounting methods bypass country legal restrictions (a Japanese config can select LIFO although only FIFO is legal), transaction-type translation is permanently broken due to a stale gettext binding, and the `rp2_config` migration tool crashes on schema-valid input containing `generators`.
3. **Hostile-to-users error UX.** Nearly every user mistake — config typo, BOM in an INI file, corrupt ODS, unknown language code — surfaces as `ERROR: Fatal exception occurred:` plus a full Python traceback. The operational layer is also stale: the CodeQL security workflow has been dead for years (still on removed v1 actions), CI tests two EOL Python versions while missing 3.13/3.14, and a packaging bug strips `Requires-Python` from published metadata.

None of this requires a redesign. The core engine's architecture is sound; the fixes are targeted. A focused effort of roughly 2–4 weeks (per the roadmap in section J) would bring this to genuinely production-grade quality.

---

## B. Critical Issues (fix immediately)

Ordered by risk. All were verified by inspection and, where noted, reproduced empirically.

1. **JP tax report: wrong tax figures with gap years / out-of-order years.** `src/rp2/plugin/report/jp/tax_report_jp.py:177-190, 347-352` — year sheets are generated in dict-insertion order (all INs, then OUTs, then INTRAs) and the carry-over formula references `'{asset}_{year-1}'` unconditionally. An asset with transactions only in 2018 and 2020 yields a `BTC_2020` sheet referencing the nonexistent `BTC_2019` → `#REF!` in the cost-of-sale cells of a tax filing. INs in 2018/2020 plus an OUT in 2019 silently produces wrong average-unit-price and carry-over numbers. **Fix:** iterate `sorted(years_2_transaction_sets)` and carry forward from the *last generated* year.

2. **JP date-filter guard uses `and` instead of `or`.** `tax_report_jp.py:98-99` — `if from_date != MIN_DATE and to_date != MAX_DATE:` means supplying only `--from_date` (or only `--to_date`) bypasses the rejection and produces a report whose moving-average carry-over silently omits pre-filter history — wrong taxable income, no warning. One-character fix.

3. **US long-term capital gains misclassification.** `src/rp2/gain_loss.py:203` + `src/rp2/plugin/country/us.py:29` — `(sale - buy).days >= 365` marks a lot long-term. IRS rules require *more than one year* by calendar date: buy 2023-02-01 / sell 2024-02-01 is exactly one year (short-term per IRS) but computes as long-term; leap-year spans (buy 2023-03-01 / sell 2024-02-29) also misclassify; time-of-day changes the result because `timedelta.days` truncates. **Fix:** compare calendar dates (`sale.date() > buy.date() + 1 year`), have country plugins return a rule rather than a day count, and add 365/366-day boundary tests.

4. **Silent transaction loss in the ODS parser.** `src/rp2/ods_parser.py:112-124` — the row after a table marker is probed by attempting to construct a transaction inside `except Exception: pass`; a first data row with any malformed field (when the user omitted the header row) is silently classified as a header and dropped from the computation. In a tax calculator, silently dropping an acquisition/disposal corrupts every downstream number. **Fix:** positively match header keywords against configured column positions; log any skipped row.

5. **Config-file accounting methods bypass country restrictions.** `src/rp2/rp2_main.py:107-118` — `-m` is validated against `country.get_accounting_methods()`, but methods from the `[accounting_methods]` config section are imported with no country check. Verified: a JP config with `2020 = lifo` runs LIFO although Japan's plugin permits only FIFO. Produces reports computed with a method not permitted in the user's jurisdiction. **Fix:** validate each configured method against the country's allowed set before `import_module`.

6. **`generators` config option is completely dead.** `src/rp2/configuration.py:181` — `if Keyword.GENERATORS.value in ini_configuration:` tests ConfigParser *section names*, not keys of `[general]`. Verified at runtime: the documented field is silently ignored, and since `-l/--plugin` is deprecated with a hard error pointing at this broken field, there is **no working way to select report generators**. **Fix:** `in ini_configuration[section_name]`, plus a test, plus decide short vs fully-qualified names (defaults are prefixed `rp2.plugin.report.`; user values wouldn't be).

7. **Transaction-type localization permanently broken.** `src/rp2/localization.py:24-41` + `src/rp2/entry_types.py:19, 73-87` — `entry_types` binds `_` at import time (before any language is set) and builds its translation map at module load; verified that with `-g es`, `TransactionType.BUY.get_translation()` still returns `"buy"`. Every localized report cell using `get_translation()` is untranslated. **Fix:** make `_` resolve the current translation at call time; build the map lazily.

8. **Spreadsheet formula injection into generated tax documents.** `src/rp2/plugin/report/abstract_ods_generator.py:173-185` — any string cell value beginning with `=` is written as a live formula. Transaction `notes`, `unique_id`, exchange/holder/asset names are user-controlled (and often imported from exchange CSVs via DaLI); a note like `=WEBSERVICE(...)` becomes an active formula in the output. Compounded by unescaped `"` interpolation in `HYPERLINK` formulas (`rp2_full_report.py:786-795`) and holder names in `SUMIF` (`open_positions.py:386`). **Fix:** write formulas only through an explicit formula API; escape quotes in all interpolations.

9. **CodeQL security scanning has been silently dead for years.** `.github/workflows/codeql-analysis.yml:42-71` — uses `github/codeql-action/*@v1` and `actions/checkout@v2`; CodeQL v1 was hard-deprecated in January 2023 and no longer runs. The repo appears security-scanned but isn't. **Fix:** upgrade to `codeql-action@v3` / `checkout@v4`, SHA-pinned.

10. **Packaging bug: published wheels declare no Python floor.** `setup.cfg:61-65` — `python_requires`, `include_package_data`, and `zip_safe` sit under `[options.packages.find]` instead of `[options]`, so setuptools ignores them (verified: `PKG-INFO` has no `Requires-Python`). pip on an ancient interpreter will install rp2 and fail at runtime. **Fix:** move all three keys into `[options]`.

---

## C. Workflow Problems

The complete user journey, with breakdown points:

**Stage 1 — Install.** `pip install rp2` per README. No venv/pipx guidance, no "verify it worked" step. (For this fork specifically: the PyPI package is upstream's — anything unique to the fork never reaches a `pip install` user.)

**Stage 2 — Prepare input (the worst stage).** The user must hand-enter every transaction into a multi-table ODS layout (IN/OUT/INTRA tables with `TABLE END` sentinels, one sheet per asset, sheet names exactly matching the `assets` config list, every timestamp with seconds *and* timezone) and hand-author an `.ini` mapping column letters to numbers. The FAQ (`docs/user_faq.md:83`) literally instructs users to copy historical prices from Yahoo/CoinMarketCap by hand. The doc's config template contradicts the shipped example (quoting convention, missing `unique_id` rows — see D). DaLI (the automated ingestion companion) is relegated to footnotes.

**Stage 3 — Run.** The user must discover which of 5 executables applies (`rp2_us`, `rp2_jp`, `rp2_es`, `rp2_ie`, `rp2_generic`); `rp2_generic` is configured through two magic environment variables (`CURRENCY_CODE`, `LONG_TERM_CAPITAL_GAINS`) undiscoverable from `--help` — and the documented Windows syntax for them isn't valid cmd/PowerShell. The README's own example command fails with a traceback unless `-n` is added (the example data goes balance-negative), teaching users to disable a safety check without explanation. `--help` says the `-m` default is `''` when it's really the country default (FIFO).

**Stage 4 — Hit an error (the loop nobody escapes).** `rp2_main.py:170-172` catches broad `Exception` and calls `LOGGER.exception`, so *every* mistake — unknown header field, duplicate INI section, BOM in the file, corrupt ODS, `-g fr` with no such catalog — prints `ERROR: Fatal exception occurred:` plus a full stack trace with the useful message buried at the bottom. Value errors don't say how to fix them (`Parameter 'exchange' value is not known: Binance` doesn't say "add it to the `exchanges` list") and don't identify the offending row. File-path errors dump the entire `--help` text after the one-line message. A corrupt ODS surfaces as the misleading `sheet BTC does not exist`.

**Stage 5 — Interpret output.** Three ODS files. Docs are decent here, but Excel can't reliably open ODS (per the FAQ) — LibreOffice is effectively required and RP2 never warns. The open-positions report requires hand-typing current prices into an "Input" sheet.

**Stage 6 — Yearly repeat.** Full history is required for cost basis, so the input file grows forever; the user must re-look-up spot prices and remember last year's exact flags (`-m` must match prior years or results silently change — and if the `[accounting_methods]` config starts later than the earliest transaction, the run dies with `Internal error: no accounting method assigned for year 2018`, blaming RP2 for a config gap). No saved profile, no "same as last year" mode.

---

## D. Usability Improvements

1. **Print clean errors for expected failures.** Catch `RP2Error` separately at the top level (`rp2_main.py:170-172`); print its message in 1–2 lines with the traceback going only to the log file. This single change fixes the majority of the "cryptic error" experience.
2. **Add remediation hints to the most-hit errors:** unknown exchange/holder → "add it to `exchanges` in <config>"; invalid transaction type → list valid values; negative balance (`balance.py:172`) → mention missing IN/INTRA transfers and the `-n` flag; accounting-method year gap (`accounting_engine.py:152`) → explain the `[accounting_methods]` config remedy instead of "Internal error".
3. **Fix documentation errors that actively mislead:**
   - `docs/user_faq.md:239` says to use transaction type `DONATION`; the valid value is `DONATE` — following the FAQ produces a validation error.
   - `docs/input_files.md:115-166` config template shows quoted values (`assets = <"asset_1_in_quotes">`), but ConfigParser treats quotes literally; the shipped `config/crypto_example.ini` is unquoted. Following the template breaks sheet-name matching.
   - The same template omits `unique_id` from all three header sections although the docs describe it and the example config maps it.
   - `README.md:174` still says "supports FIFO, LIFO and HIFO" — LOFO is missing (it *is* in `supported_countries.md` and the live `--help`). No CHANGELOG entry for LOFO; `setup.cfg:5` description also omits it (and misspells "menthods").
   - Four broken intra-doc anchors (README→FAQ fee link with a doubled "h"; `user_faq.md:139` "instead-on"; `output_files.md:81` stale anchor; `supported_countries.md:24` case-sensitive `#Spain`).
4. **Fix `-m` help text** (`rp2_main.py:306-314`) to state the real default, and make `-g` list available languages.
5. **Replace `rp2_generic`'s env-var interface** with CLI flags (`--currency-code`, `--long-term-days`); fix the invalid Windows instructions in `supported_countries.md:47-50`.
6. **Stop printing full `--help` after path errors** (`rp2_main.py:373-395`); check existence before extension so `input.xlsx` gets the right message.
7. **Add a `--check` dry-run mode** that validates config+ODS and reports *all* errors with row/column references, instead of dying on the first one.
8. **Warn at generation time** that outputs are ODS and Excel compatibility is limited; link the FAQ entry.
9. **Message-wording fixes:** "positive integer was expected" for column 0 (`configuration.py:249-251` — 0 is valid, say "non-negative"); JP error omitting IntraTransaction (`tax_report_jp.py:310`); `transaction_set.py:34` hardcodes "IN transaction set" for all set types; stray quote in `abstract_ods_generator.py:103`.

---

## E. Code-Level Findings (file-by-file)

Severity: **C**=critical, **H**=high, **M**=medium, **L**=low. Items in section B are not repeated in full.

### Core engine & data model

**`src/rp2/gain_loss.py`**
- L203 **[C]** Long-term threshold `days >= 365` — see B3.
- L40 **[M]** `non_zero=True` validation uses RP2Decimal's 1e-13-tolerant `==`, so a dust-sized taxable fraction (e.g. 4e-14 crypto fee on an intra transfer) is *taxable* per `is_taxable()` yet *rejected* as "zero value", killing the whole run on one dust row. Align the taxability check and the validator on the same quantization.
- L77-81 **[L]** `__eq__` raises `RP2TypeError` instead of returning `NotImplemented`; L156 `taxable_event_fiat_amount_with_fee_fraction` apportions a fee-*exclusive* amount over a fee-*inclusive* denominator — name implies the opposite.
- L97-122 **[L]** `to_string` scaffolding duplicated across 4 classes (also `in_transaction.py:123-149`, `out_transaction.py:125-149`, `intra_transaction.py:76-104`). Hoist into `AbstractTransaction`.

**`src/rp2/rp2_decimal.py`**
- L36-42 **[H]** Defines quantized `__eq__` with no `__hash__` → `hash(RP2Decimal("1"))` raises `TypeError` (verified). `Balance` (`balance.py:35`) and `_YearlyGainLossAmounts` (`computed_data.py:105`) are frozen/eq dataclasses with RP2Decimal fields whose generated `__hash__` explodes on first set/dict use. Deeper issue: tolerance-based equality is non-transitive, so a consistent hash is impossible — keep exact `Decimal` equality and expose `approx_eq()` for the engine.
- L33-58 **[M]** All comparisons `.quantize(CRYPTO_DECIMAL_MASK)` under `prec=31`: verified `RP2Decimal("1e40") > ZERO` raises raw `decimal.InvalidOperation`. Plausible for meme-token unit balances; running sums amplify. Catch and re-raise as `RP2ValueError`, or compare via subtraction.
- L29-30 **[M]** Mutates the *importing thread's* decimal context at class-body execution: other threads silently get prec=28 with no float trap; also side-effects host apps that embed rp2. Use a dedicated `Context` + `localcontext()` around `compute_tax`.

**`src/rp2/computed_data.py`**
- L132 **[H]** `if crypto_in_running_sum is not ZERO` — identity comparison against the singleton; any arithmetic produces a new object (verified), so a numerically-zero sum divides by zero with a raw `decimal.DivisionByZero`. Use `> ZERO`.
- L104-105 **[L]** Comment says "Frozen and eq are not set because we need to modify fields" directly above `@dataclass(frozen=True, eq=True)`; nothing mutates it.
- L168-171, 184-187 **[L]** Four grand totals accumulated and never read — dead code. L189 `list(sorted(...))` is redundant.
- L135-136, 216 **[L]** Yearly summaries filter by `year >= from_date.year` while the detailed set filters by full date — mid-year `from_date` makes the two report views disagree.

**`src/rp2/in_transaction.py`**
- L57-62 **[H]** Negative/zero `crypto_in` deliberately allowed for STAKING ("staking income can be negative"), but STAKING is earn-typed → always taxable → `tax_engine.py:127-128` / `gain_loss.py:40` reject non-positive amounts. The promised feature is a guaranteed crash; no test covers it. Reject at construction with a clear message, or handle in the engine.
- L63-64 **[L]** `type_check_positive_decimal(...) if crypto_fee else ZERO` — truthiness test silently converts falsy garbage (`0`, `0.0`, `""`) to `ZERO` instead of type-erroring; inconsistent with the `is not None` test at L71. L210-211 `is_crypto_fee_defined` returns `> ZERO`, not "was passed", contradicting its name/comment.

**`src/rp2/abstract_transaction.py`**
- L42-44 **[M]** `row` defaults to `id(self)` which becomes `internal_id` — nondeterministic across runs (unstable ordering/AVL keys), and `__eq__` (L62) compares `internal_id` only, without a class check: an `InTransaction` can equal an `OutTransaction`. The in-code TODO already says to make `row` mandatory — do it.

**`src/rp2/abstract_entry_set.py`**
- L54-61 **[M]** `duplicate()` is `copy(self)` — shares `_entry_list`, `_entry_set`, `_entry_to_parent` with the original. `add_entry` on the original after `duplicate()` grows the copy while its `__is_sorted` flag stays True → silently unsorted iteration. Works today only by call-order accident. Copy the containers or freeze after duplication.
- L178-187 **[L]** Filtered iteration re-skips all pre-`from_date` entries linearly on every fresh pass (report generators iterate repeatedly); bisect the start index once. L190-191 sort key is timestamp-only — add `internal_id` tiebreaker for determinism.
- L183-185 **[M]** Date filtering uses each timestamp's *own* timezone while ordering is absolute (same in `gain_loss_set.py:118`, `computed_data.py:127,150,153`, `balance.py:134`): two rows at the same instant entered in different timezones land in different tax years. The accounting engine normalizes to UTC (`accounting_engine.py:138`) — the rest of the system should too, or validate homogeneity.

**`src/rp2/gain_loss_set.py`**
- L71-83 **[M]** Both fraction getters test membership *before* `_check_sort()`, but the dicts are only populated by `_sort_entries()` — called on a fresh set they raise spurious `Unknown transaction` for valid entries (`get_transaction_type_count` at L56-59 does it right). L61-64 can leak a raw `KeyError` for entries beyond `to_date`.
- L46-47 **[L]** `__taxable_events_to_fraction` / `__acquired_lots_to_fraction` are keyed by `GainLoss` — names invert the mapping. L56-59 counts gain-loss *fractions*, not transactions (a 3-lot sell counts SELL 3×) — rename or fix.

**`src/rp2/balance.py`**
- L136-140 **[M]** `InTransaction.crypto_fee` is ignored by balance computation; docs require a compensating FEE out-transaction but nothing validates it exists → silently overstated balances and an untaxed fee disposal. Warn when `crypto_fee > 0` with no matching FEE row.
- L111 **[L]** Parameter misnamed `"in_transaction_set.asset"` (it's `input_data.asset`). L244-245 `f"{exchange}_{holder}"` sort key ambiguous when names contain `_` — use a tuple. `BalanceSetIterator` (L229) lacks `__iter__`.

**`src/rp2/tax_engine.py`**
- L113-190 **[L]** `total_amount` exists only for DEBUG logs but its arithmetic always runs; three nearly identical log/add blocks could be one parameterized block.

**`src/rp2/transaction_set.py`** — L34 **[L]** hardcoded "IN transaction set is empty" for all set types; L28 omits the parameter name.

**`src/rp2/intra_transaction.py`** — L46-48 **[L]** comment says "if the fee is 0, raise an exception"; the check is the opposite.

### Configuration, parsing, entry point

**`src/rp2/configuration.py`**
- L181 **[C]** Dead `generators` option — see B6.
- L157-167 **[M]** Deprecated-JSON detection catches only `JSONDecodeError`; schema-violating JSON (verified: `[1,2]`) escapes as a raw `ValidationError` traceback, bypassing the intended "run rp2_config" message. Catch `ValidationError` too.
- L379-388 **[H]** `dateutil.parser.parse` defaults missing date parts to *today* (verified: `"12:30 +00:00"` → today at 12:30) — a partial-date cell becomes a transaction dated the run day: nondeterministic, silently wrong lots. Pass an impossible `default` sentinel and reject, or require full ISO-8601.
- L240-269 **[M]** Header sections never checked for *required* columns — an `[in_header]` missing `timestamp` passes config load and dies later with a raw `TypeError`. The JSON schema encodes the required lists; the INI path discarded that knowledge.
- L170 **[M]** `ini_configuration.read()` uses platform-default encoding while the JSON probe used UTF-8; a UTF-8-BOM file (Windows Notepad default) fails with `MissingSectionHeaderError: '﻿[general]'` (verified). Use `encoding="utf-8-sig"`. Wrap in `except configparser.Error` → `RP2ValueError` (duplicate sections/options currently traceback).
- L227-238 **[L]** Dead branches (`str.split` never returns `[]`; post-split elements are always `str`) and the function returns `set(list_as_values)` instead of the duplicate-checked `result` set it built.
- L271-287 (+`accounting_engine.py:149-152`) **[M]** Transactions predating the first `[accounting_methods]` year die as `Internal error: no accounting method assigned for year N` — a user config problem labeled as an internal bug. Validate coverage at load.
- L414-435 **[L]** `type_check_string_or_integer` also accepts float and its message says "non-string"; `type_check_positive_int` says "non-positive" while accepting 0.

**`src/rp2/rp2_main.py`**
- L107-118 **[C]** Country bypass via config methods — see B5.
- L170-172 **[H]** Single `except Exception` → traceback UX for all expected errors — see D1.
- L67, 373-395 **[M]** `set_generation_language` and `_setup_paths` run *outside* the fatal-error handler: `-g fr` and mkdir `PermissionError` produce raw uncaught tracebacks. Move inside; use `mkdir(parents=True, exist_ok=True)`.
- L202-222 **[M]** `generators.remove(plugin_name)` happens before the `hasattr(output_module, "Generator")` check — a module lacking `Generator` is silently skipped and the final not-found check can't catch it. Remove only after successful run.
- L119-127 **[L]** Accounting-method range log misattributes methods to ranges (prints the *new* method for the *previous* range) and doesn't sort years.
- L330-338 **[L]** `-p/--prefix` accepts path separators — `-p ../../foo_` writes (and pre-deletes) files outside the output dir. Reject `os.sep` and `..`.
- L306-314 **[L]** `-m` help shows `default: ''`.

**`src/rp2/ods_parser.py`**
- L112-124 **[C]** Header heuristic can drop a transaction — see B4.
- L44, 51-52 **[M]** ezodf opens garbage files without error (verified) → misleading "sheet does not exist". Check `doc.mimetype`/nonempty sheets; consider a zip-ratio guard against decompression bombs.
- L70-82 **[L]** Materializes every row of the sheet's declared extent (a hostile `number-rows-repeated` in the millions → O(rows×cols) memory); stop after the last `TABLE END`.
- L106 **[L]** Duplicate table detection misses header-only duplicates (guarded by `is_empty()`); track seen types in a set. L314-315 `TABLE END` matching is whitespace/case-sensitive with a misleading error on mismatch. L322-327 dead `main()` stub.

**`src/rp2/localization.py` / `src/rp2/entry_types.py`** — **[C]** stale `_` binding breaks transaction-type translation — see B7.

**`src/rp2/logger.py`**
- L21-38 **[M]** Import side effects: creates `./log/` and opens the log file even for `--help` (crashes in a read-only CWD); invalid `LOG_LEVEL` env var crashes at import with a raw `ValueError` (verified). Use `FileHandler(..., delay=True)`, lazy mkdir, whitelist validation.
- L34 + `ods_parser.py:82`, `rp2_main.py:89,152,155` **[M — privacy]** At DEBUG, every spreadsheet row, holder name, and full computed dataset is written to a world-readable, never-rotated `./log/rp2_*.log`. For a privacy-focused tax tool, users attaching debug logs to GitHub issues leak their complete financial history. Create logs `0o600`, document loudly, consider redaction.

**`src/rp2/rp2_configuration_translator.py`**
- L66-67 **[H]** Assigns a JSON *list* as a ConfigParser value → `TypeError` crash on any config using the optional `generators` array (verified) — the exact tool the deprecation message tells users to run. `", ".join(...)`.
- L46-49 **[L]** exists-check/open("w") TOCTOU — use `open(..., "x")`. Zero tests exist for this shipped console entry point.

**`src/rp2/configuration_schema.py`** — L15-17, 250-255 **[M]** No top-level `required` (verified `{}` validates, then `rp2_config` crashes with `KeyError`); `accounting_methods` year patterns unanchored and reject pre-2000 years the INI path accepts. Add `required`, `additionalProperties: false`, anchor `^...$`.

**`src/rp2/plugin/country/es.py`** — L28-29 **[M]** Declares a 365-day long-term period; Spain taxes crypto as savings income with *no* holding-period distinction (JP/IE correctly return `sys.maxsize`). ES reports imply a distinction Spanish law doesn't have. Verify with a Spanish tax source and fix. Also note `jp.py:34-41` self-documents its accounting method as "incorrect and only a placeholder" — known-shipping-wrong, must be tracked.

**`src/rp2/plugin/country/generic.py`** — L69 **[L]** Comment says "US-specific entry point"; `ie.py:46` returns `en_IE` where the contract (`abstract_country.py:90`) promises ISO 639-1.

**`src/rp2/rp2_error.py`** — L24-25 **[L]** `__repr__` returns the bare message, losing the class name in logs.

### Report generators

**`src/rp2/plugin/report/abstract_ods_generator.py`**
- L173-185 **[H]** Formula injection — see B8. L179-181 **[L]** every RP2Decimal cast to `float` before writing (double rounding in a tax document).
- L55, 88 **[H]** `years_2_accounting_method_names[MIN_DATE.year]` assumes the single entry is keyed 1970; a config with a single `2020 = fifo` entry raises `KeyError: 1970` and kills the run. Use `next(iter(...values()))`.
- L84-103 **[M]** Legend fill scans `range(0,100)` for a cell equal to the *localized* string `_("Accounting Method")` then writes dates positionally at +1/+2: magic 100 (the US legend sheet is already 113 rows), silent misplacement if the legend is reordered, and a `.po` update without template regeneration breaks the lookup (or raises a raw ezodf `IndexError` on short sheets). Search `sheet.nrows()`, locate rows by their own labels, match a non-localized sentinel.
- L56-58 **[M]** Output file is built by raw prefix concatenation (no validation — pairs with the `-p` traversal above) and `unlink()`ed *before* generation: a mid-generation failure leaves the user with no report at all. Write to a temp file, atomically replace.
- L113-136 **[L]** `country is None` branch and the `.txt` template-link mechanism are exercised by nothing in the repo — dead until tested. L52 `isinstance(x, typing.Set)` — use `collections.abc.Set`.

**`src/rp2/plugin/report/rp2_full_report.py`**
- L215-216 **[H]** Summary headers hardcode `"USD"`/`"USD Total"`; this generator also runs for JP/IE/ES → JPY/EUR totals labeled USD. The per-asset version (L367-368) does it correctly. Parameterize on `currency_iso_code`.
- L70-87 **[M]** Row maps and header lists are *class-level* mutable state mutated per run and never reset — a second `generate()` in one process resolves hyperlinks against the previous run's rows (`tax_report_jp.py:82-85` does it right in `__init__`).
- L979 **[M]** `range(12, 19)` blanks the no-acquired-lot block but the block spans columns 12-19 — column 19 keeps stale content/style. Use `range(12, 20)`.
- L786-795 **[M]** `HYPERLINK` formula interpolation with no `"` escaping — see B8.
- L566-569 **[L]** `year` is updated *before* `__get_border_style(entry.timestamp.year, year)`, so the comparison always ties and the year-boundary border on "Sent/Sold %" never renders (`__generate_gain_loss_summary` L707-709 does it correctly).
- L553-696, 802-984 **[M — perf]** Every cell write runs ~5 redundant type checks plus two ezodf XML lookups; sheets are allocated at `MAX_COLUMNS`=40 when ≤20 are used. Hoist validation out of the hot loop, cache row objects.

**`src/rp2/plugin/report/us/tax_report_us.py` vs `ie/tax_report_ie.py`**
- **[H]** 97% verbatim duplication: the two ~200-line files differ in exactly 6 lines (logger name, output/template names, date format). Extract a shared base whose subclasses supply three class attributes (~15 lines per country). The ~55-line `generate()` skeleton is additionally repeated in `rp2_full_report.py:428-480`, `open_positions.py:164-196`, and `jp` — it belongs in `AbstractODSGenerator` as a template method.
- L123, 137 **[M]** Literal `"Legend"` compared against sheet names, but the framework renames the sheet to localized `_("Legend")` — works only while US/IE ship English-only templates; first translated template → `KeyError`.
- L143, 198 **[L]** `border_suffix` cleared after the first row of the first sheet touched per asset — other sheets lose their separator border. L136-141 **[L]** each of 11 sheets grows `MIN_ROWS+count+1` rows per asset (~20N trailing blanks). L73-76 **[L]** `MAX_COLUMNS` unused; `HEADER_ROWS=7` and interleaved column literals with no named mapping.

**`src/rp2/plugin/report/jp/tax_report_jp.py`**
- L177-190, 347-352 **[C]** Year ordering/gap carry-over — see B1. L98-99 **[C]** `and`-vs-`or` guard — see B2.
- L194-200, 338-409, 431-437 **[H]** The whole computation is stitched from hardcoded 1-based formula anchors (`E13/F13/G13/H13`, `row_index+3/+8/.../+21`, initial `row_index=21`); a row inserted in the template silently shifts every SUM — the file opens fine, the numbers are wrong. Add named anchor constants and assert on known label cells at generation time.
- L304, 323-331 **[M]** Writes the *string* `"0 (￥1,234.00)"` into the numeric sales column, relying on LibreOffice treating text as 0 in SUM; fragile cross-row variable hand-off. Write numeric 0; put the yen amount in a note.
- L72-78 **[L]** `MIN_ROWS`/`MAX_COLUMNS`/`TRANSACTION_ROW_START` dead — and `TRANSACTION_ROW_START=22` contradicts the real start 21 (an off-by-one memorialized as a dead constant). L310 error message omits IntraTransaction.

**`src/rp2/plugin/report/open_positions.py`**
- L307, 329 **[M]** Generated `IF(VLOOKUP(...)...` formula is missing its closing paren — LibreOffice auto-repairs it; stricter ODS consumers error.
- L273-278 **[M]** `asset_crypto_balance_holder[asset]` KeyError when an asset has unsold cost basis but no holder with positive balance (reachable with `--allow_negative_balances`); `cost_basis / total_crypto_balance` has no zero guard. Skip with a logged warning.
- L47-53 **[M]** Same class-level mutable-state pattern as `rp2_full_report`. L386, 459 **[L]** holder names unescaped in `SUMIF`. L39 **[L]** `"Enter asset value"` not localized but exact-string-matched by the IF formula — localizing surrounding strings later silently breaks it. L254 **[L]** loop variable `balance_set` is a single `Balance`.

### Tests, CI, packaging

**`tests/`**
- **[H]** Golden tests are all-or-nothing and slow: each class's `setUpClass` regenerates ~50 reports via subprocess before any test runs (one selected test still costs 32s; full suite plausibly 10-20 min × 15 CI jobs). No pytest markers, no xdist, no pytest config at all. Mark `slow`/`golden`, generate lazily, parallelize.
- **`tests/ods_diff.py:57-62`** **[M]** Golden comparison rounds through `float` (`round(float(value), 13)`) — float64 carries ~15-17 significant digits, so a 1-ulp cost-basis regression on large fiat values passes every golden test. For tax software this is the core verification path. Compare as `decimal.Decimal` quantized strings.
- **`tests/ods_diff.py:115-120`** **[L]** `NamedTemporaryFile(delete=False)` leaks a file per comparison; ASCII dumps always generated even for passes.
- **[M]** No direct unit tests for: `accounting_engine.py` (tested only via FIFO-only `test_tax_engine.py` — AVL same-timestamp disambiguation, year-lookup error path, and exhaustion paths untested), `abstract_accounting_method.py` (the entire feature-based machinery behind HIFO/LIFO/LOFO — golden-only), `computed_data.py`, `input_data.py`, `rp2_configuration_translator.py` (zero tests for a shipped entry point), `logger.py`; `open_positions` is golden-verified only for jp/kl locales, never US or non-FIFO.
- **LOFO verdict:** real integration coverage (29 golden files across US+generic, all diffed in CI; verified the fixtures genuinely discriminate — the lofo golden differs from fifo by 157 lines and from hifo by 212), but zero unit-level assertions of its sort semantics ("lowest" = lowest `spot_price`, fees excluded — undocumented), no year-switch coverage (`test_data_multi_method.ini` maps only fifo/lifo/hifo), and the golden files were generated by the same commit that introduced the code — regression guards, not correctness proofs. Add a direct `sort_key`/selection-order unit test, a lofo year in the multi-method config, and hand-verify one golden.
- **`tests/abstract_test_ods_output_diff.py:43-44`** **[L]** Stale comment ("Temporarily removed lifo and hifo") directly above a METHODS list containing both.

**`.github/workflows/`**
- `codeql-analysis.yml` **[H]** dead since 2023 — see B9.
- `documentation_check.yml:10-11` **[H]** `actions/checkout@main` (mutable branch ref) and a third-party action pinned to a mutable tag — supply-chain exposure; pin to SHAs, add `permissions: contents: read`.
- `unix_unit_tests.yml:11` / `windows_unit_tests.yml:11` **[H]** Matrix tests EOL Pythons 3.8/3.9, missing 3.13/3.14 — the versions real users run are untested. `static_analysis.yml:11` lints only on EOL 3.9.
- All workflows **[M]** deprecated `checkout@v2`/`setup-python@v2`, no `permissions:` blocks, no `concurrency:` cancellation (duplicate push+PR runs), no pip caching. L14 **[L]** `PYTHONPATH: ./src:./test` — the directory is `tests`; works by accident.
- **[M]** No Dependabot/Renovate, no coverage measurement (nobody knows what fraction of the gain/loss logic is covered), no release workflow.

**Packaging**
- `setup.cfg:61-65` **[H]** `python_requires` et al. in the wrong section — see B10.
- `setup.cfg:40-46` **[M]** Lower-bound-only pins: `jsonschema>=3.2.0` spans the 3→4 API break; `pyexcel-ezodf` (the *only* ODS backend) last released ~2017 — an abandonment risk needing a contingency; `dev` extras fully unpinned → CI toolchain drift breaks builds nondeterministically.
- `Makefile:51-63` + `README.dev.md:197-212` **[M]** Release = deprecated `setup.py sdist bdist_wheel` + manual twine + hand-edited checklist. Move to `python -m build` and a tag-triggered workflow with PyPI Trusted Publishing.
- `.pre-commit-config.yaml` **[L]** flake8 3.9.2 (2021), deprecated isort mirror, black 22.3.0, pyupgrade `--py36-plus` (below even the declared 3.8 floor). `pyproject.toml` **[L]** holds only build-system+black; metadata is legacy setup.cfg; no pytest config anywhere. `MANIFEST.in` **[L]** ships 108+ binary golden files in every sdist.

---

## F. Architecture Review

**What's clean and should be preserved:**
- Layering is genuinely good: immutable transaction entities → lazily-sorted entry sets with fraction bookkeeping → AVL-backed accounting engine → tax engine → computed data → pluggable report generators. Module boundaries are easy to reason about.
- Three orthogonal plugin axes (country, accounting method, report generator) with minimal plugin surface — LOFO is a 3-line plugin, which is exactly what a good abstraction produces. Per-year accounting-method switching via an AVL year map is elegant.
- `RP2Decimal` trapping float contamination process-wide is the right instinct for money math. Strict mypy (`disallow_any_*` nearly maxed) and pervasive `type_check_*` guards are real strengths.
- The `__`-prefixed template-sheet keep/rename/delete protocol and the `__styles` sheet trick are a clever way to ship styled ODS templates.

**Structural weaknesses:**
1. **The Python↔template contract is entirely implicit.** Row anchors, sheet names, style names, header heights, and localized label strings couple code to binary `.ods` templates with zero validation — failures are either silent wrong numbers (JP anchors) or opaque `KeyError`s. This is the single biggest architectural debt. A per-plugin template manifest (expected sheets, anchor-label cells, required styles) validated once at `_initialize_output_file` time would convert every silent failure into a fast, clear one.
2. **Duplication where the framework should own the skeleton:** US/IE reports are 97% identical; the `generate()` skeleton is repeated 5×; `to_string` scaffolding 4×; the tax-engine loop 3×.
3. **Class-level mutable state in older report plugins** (`rp2_full_report`, `open_positions`) makes generators single-use-per-process; the newer JP plugin already models the fix.
4. **Split-brain configuration validation:** the JSON schema knows required headers/anchored patterns; the INI path re-implements validation and dropped those rules (see M-items). One source of truth should drive both.
5. **Hidden global state:** decimal context and gettext `_` binding both mutate import-time globals, and both have verified bugs as a result (thread precision loss; broken entity translation).

**Verdict:** the architecture is scalable and maintainable at its core; the debt is concentrated in the report layer's template coupling and duplication, not in the engine.

---

## G. Performance Fixes

For a local CLI at typical scale (thousands of transactions), performance is adequate; these are the real wins, in order:

1. **Test-suite wall time (biggest practical cost):** lazy per-test golden generation, pytest-xdist, `slow` markers, pip caching in CI. Today ~10-20 min × 15 jobs per push.
2. **`_fill_cell` hot path** (`rp2_full_report.py`, all generators): ~5 redundant type checks + 2 ezodf XML child lookups per cell × ~20 columns × tens of thousands of fraction rows → >1M redundant calls. Validate once per table, cache row objects, size sheets to actual columns instead of `MAX_COLUMNS=40`.
3. **Filtered-set iteration** (`abstract_entry_set.py:178-187`): O(skipped) restart on every pass; bisect the start index at sort time. Report generators iterate the same sets repeatedly.
4. **ODS parsing** (`ods_parser.py:70-82`): stop materializing rows after the final `TABLE END`; guards against declared-extent blowups double as a hostile-file defense.
5. **Debug-log arithmetic always evaluated** (`tax_engine.py`): guard `total_amount` accumulation with `isEnabledFor(DEBUG)` or delete.
6. Trailing-blank-row over-allocation in us/ie generators (~20 rows per sheet per asset).

---

## H. Security Concerns

Context: local CLI processing the user's own files — no network, no auth, no server. Verified explicitly: **no arbitrary-import or code-injection path exists** through config-driven plugin loading (method names are confined to `rp2.plugin.accounting_method.`; report modules must be discovered inside `rp2.plugin.report`), and `cProfile.runctx` evaluates a fixed string. The real exposure is:

1. **Spreadsheet formula injection (highest):** `=`-prefixed user data becomes live formulas in generated documents; unescaped `"` in HYPERLINK/SUMIF interpolations. Tax reports get shared with accountants — a poisoned exchange-CSV note could exfiltrate via `WEBSERVICE()` when the recipient opens the file. Fix per B8.
2. **Privacy of debug logs:** full transaction history, holders, and balances in world-readable, never-rotated `./log/` files (M6 above). This is the project's own headline value proposition — treat as high priority.
3. **CI supply chain:** `actions/checkout@main` and mutable third-party tags can exfiltrate the repo token; no `permissions:` least-privilege blocks; CodeQL dead since 2023. SHA-pin everything, restore CodeQL, add Dependabot.
4. **Hostile input files:** ezodf opens non-ODS garbage silently; no decompression-bomb guard; declared-extent row blowups (L5). Low likelihood (users process their own files) but cheap to fix alongside the misleading-error fix.
5. **Path handling:** `-p/--prefix` traversal writes/deletes outside the output directory; pre-generation `unlink()` creates a data-loss window. Validate prefix; write-temp-then-rename.
6. **Dependency risk:** `pyexcel-ezodf` unmaintained since ~2017 and is the sole ODS backend; `jsonschema>=3.2.0` spans a major API break; published metadata missing `Requires-Python` (B10).

---

## I. Automation Opportunities

Ranked by friction removed:

1. **Data ingestion as the primary path.** Promote DaLI from footnote to the documented default workflow (exchange CSVs → DaLI → ready-to-run ODS+ini); the manual spreadsheet path should be the fallback, not the tutorial.
2. **Spot-price assistance.** Users hand-copy historical prices from Yahoo/CoinMarketCap and hand-type current prices into the open-positions Input sheet. An opt-in (privacy-respecting, offline-file-capable) price plugin removes the most tedious per-row step.
3. **Config generation & dry-run validation.** `--generate-config input.ods` (infer headers/assets/exchanges/holders — the ini mostly restates the ODS) and `--check` (validate everything, report *all* errors with row/column references).
4. **Friendly-error layer.** Automated remediation hints (D2) — cheap, transforms the worst workflow stage.
5. **Release automation.** Tag-triggered build+publish via PyPI Trusted Publishing; bumpversion-driven changelog gate. Today the checklist is fully manual and uses a deprecated build invocation. One feature (LOFO) already left version/CHANGELOG/README inconsistent — automation is how that stops recurring.
6. **Docs-consistency CI.** Extend `documentation_check.yml` to validate intra-doc anchors (4 broken today) and to diff documented CLI flags/method lists against `argparse`/plugin registries.
7. **Coverage + Dependabot + concurrency-cancellation in CI** — standard hygiene currently absent.
8. **Yearly-repeat ergonomics:** `--tax-year 2025` shorthand for `-f/-t`; promote config-file `[accounting_methods]` over `-m` in docs so the annual command is stable.

---

## J. Refactor Plan

**Fix immediately (correctness of tax output & dead features — ~2-3 days):**
1. JP year-ordering/gap carry-over (B1) and `and`→`or` date guard (B2).
2. Long-term gains day-count rule + boundary tests (B3).
3. ODS header-heuristic silent row drop (B4).
4. Config accounting-method country validation (B5).
5. `generators` membership test (B6) + test.
6. `KeyError: 1970` single-entry accounting-method crash (`abstract_ods_generator.py:55,88`).
7. Hardcoded "USD" Summary headers (`rp2_full_report.py:215-216`).
8. `is not ZERO` identity guard (`computed_data.py:132`).

**Fix this week (error UX, security, CI/packaging — ~1 week):**
9. Clean `RP2Error` top-level handling + remediation hints (D1-D2); move `-g`/path setup inside the handler; `utf-8-sig` config reads; configparser error wrapping.
10. Formula-injection hardening: explicit formula API + quote escaping (B8).
11. CI modernization: CodeQL v3, SHA-pinned actions, Python 3.10-3.14 matrix, permissions blocks, concurrency, pip cache, Dependabot.
12. `setup.cfg` section fix (B10); `python -m build`; pin dev extras.
13. Doc corrections: DONATION→DONATE FAQ, config template quoting/`unique_id`, README LOFO mention, broken anchors, CHANGELOG entry.
14. Log privacy: `0o600` log files, DEBUG-content warning, `delay=True` handler, `LOG_LEVEL` validation.
15. dateutil partial-date rejection (H6) and corrupt-ODS detection (M7).

**Fix this month:**
16. Extract shared US/IE report base class + `generate()` template method; migrate class-level mutable state to `__init__` in `rp2_full_report`/`open_positions`.
17. Template-contract validator (per-plugin manifest of sheets/anchors/styles, checked at init) — converts every silent template failure into a fast error; then de-magic the JP anchors.
18. Localization fix (call-time `_`, lazy translation maps) + a non-English golden test for entity translation.
19. Test debt: direct unit tests for `accounting_engine` (all 4 methods, same-timestamp disambiguation, exhaustion paths), `abstract_accounting_method`, LOFO sort semantics, `rp2_configuration_translator`; decimal-safe `ods_diff`; pytest markers + xdist; coverage gate.
20. Config validation unification (required header columns on INI path; schema `required`/anchoring; JSON translator `generators` fix).
21. Negative-staking decision: reject clearly or implement; dust-amount tolerance alignment (M5); `RP2Decimal.__hash__` strategy (H4).
22. Timezone policy for tax-year attribution (M7-core) — decide, document, enforce.

**Improve later:**
23. `--check` dry-run and `--generate-config`; `--tax-year` shorthand; `rp2_generic` CLI flags replacing env vars.
24. `_fill_cell` hot-path optimization; filtered-iteration bisect; parser early-exit.
25. PEP 621 `pyproject.toml` migration; pre-commit autoupdate; release workflow with Trusted Publishing.
26. DaLI-first documentation restructure; price-plugin design; ES long-term-period verification with a tax professional; JP accounting-method placeholder resolution.
27. Sync fork with upstream v1.7.2+ (LOST transaction type) and decide the fork's purpose (contribution branch vs. long-lived divergence).

---

## K. Final Grade (1–10)

| Category | Grade | Rationale |
|---|---|---|
| Code quality | 7 | Strict typing, pervasive validation, readable — but verified bugs (identity compare, dead option, stale i18n binding, broken `__eq__` contracts) and 4-5× duplicated scaffolding. |
| Architecture | 8 | Clean layering and a genuinely good triple plugin system; docked for implicit template contracts and class-level state in older plugins. |
| Usability | 4 | Traceback-as-error-UX, hand-authored config that contradicts its own docs, 5 executables + env-var configuration, FAQ instructing an invalid value. |
| Workflow | 4 | Manual transaction entry and manual price lookup dominate; yearly repeat restarts from the worst stage; DaLI integration buried. |
| Security | 6 | No injection/RCE path (verified) and safe plugin confinement — but formula injection in outputs, world-readable financial debug logs, dead CodeQL, mutable action refs. |
| Performance | 6 | Fine at expected scale; AVL engine is O(n log n); docked for hot-path cell writes, O(skipped) iteration restarts, and a 10-20 min test suite. |
| Scalability | 6 | Engine scales; large-magnitude decimals raise raw `InvalidOperation`; parser materializes declared extents; single-threaded assumptions baked into global decimal context. |
| Maintainability | 7 | Strong module boundaries and mypy; docked for template coupling, US/IE copy-paste, split-brain config validation, and misleading names. |
| Automation readiness | 3 | Data entry, price lookup, config authoring, release, and changelog are all manual; no coverage, no Dependabot; docs-consistency drift already visible after one feature. |
| Production readiness | 5 | Core engine yes; JP report, LT-gains rule, silent row drop, and country-bypass must land first — plus packaging metadata and clean errors — before trusting output blind. |

**Overall: 5.6/10 — strong engine, weak edges.** The gap between "architecturally sound" and "production-ready" here is almost entirely edge handling, report-layer contracts, and operational hygiene.

---

## L. Perfect-State Blueprint

What this system looks like when the roadmap is done:

**Correctness.** Every country rule (long-term threshold, JP moving average, ES treatment) is encoded as a reviewed, unit-tested rule object with boundary tests at 365/366 days and gap-year fixtures. Golden files are decimal-exact (no float round-trip), independently hand-verified per method, and complemented by direct unit tests on the accounting engine covering all four methods, same-timestamp disambiguation, and exhaustion paths. Coverage is measured and gated.

**Trustworthy failure modes.** No silent path exists: template contracts are validated at generator init (manifest of sheets, anchors, styles); the parser positively matches headers and logs every skipped row; partial dates, corrupt files, and BOMs are rejected with one-line, remediation-bearing messages. `RP2Error` prints a sentence; only genuine internal bugs print tracebacks. A `--check` mode validates the whole input and lists every problem with row/column references.

**A humane workflow.** The documented happy path is: DaLI ingests exchange data → `rp2_us --check` validates → `rp2_us --tax-year 2026` runs with config-file-pinned accounting methods → three reports open cleanly, Summary labeled in the right currency, with prices assisted by an opt-in offline-capable plugin. The generic country is configured by flags, not env vars. Docs, help text, and plugin registries can't drift because CI diffs them.

**A hardened edge.** Output cells are data unless explicitly formulas; all interpolations escaped; logs are `0o600` with a prominent DEBUG-privacy warning; prefixes validated; reports written atomically. CI runs on supported Pythons with SHA-pinned, least-privilege, cached, concurrency-deduped workflows; CodeQL and Dependabot actually run; releases are tag-triggered via Trusted Publishing with an enforced changelog.

**A smaller, sharper codebase.** One report-generation skeleton in the framework; each country report ~15 lines of declarative attributes plus its genuinely unique logic; one source of truth for config validation shared by INI and JSON paths; `RP2Decimal` with exact equality plus an explicit `approx_eq`; no class-level mutable plugin state; no dead code. Adding country #6 means writing a rule object, a template, a manifest, and golden fixtures — nothing else.

That end state is reachable incrementally; nothing in it requires abandoning the current architecture.
