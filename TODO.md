# TODO

Single tracked backlog for this repository. Items were gathered from the open
pull requests and the branches behind them — `AUDIT.md` (PR #3),
`MONITORCLT_PLATFORM_ROADMAP.md` (PR #2), the agent-layer specs under
`tools/monitorclt_probate/agents/` (PR #10) — plus the follow-ups named in
`tools/monitorclt_probate/README.md` on `main` and the `TODO` comments in the
source.

Two backlogs live here. Sections 2–7 are the rp2 tax calculator; sections
8–11 are MonitorCLT, a real-estate platform that shares this repository but
whose main implementation is not on GitHub at all.

Conventions: `[ ]` open, `[x]` done. Keep the source reference on each item so
it stays checkable. Add new items under the right section rather than at the
bottom.

Last reviewed: 2026-09-03.

---

## 1. Open pull requests

Seven PRs are open against `main`, the oldest from July. Each needs a decision:
land it, revise it, or close it.

- [ ] **PR #1** — Adopt engineering practices from worldmonitor evaluation
      (`claude/worldmonitor-repo-eval-nvl0w2`). Adds `ARCHITECTURE.md`,
      `AGENTS.md`/`CLAUDE.md`, `docs/worldmonitor_evaluation.md`, and a
      CI-enforced `scripts/check_documentation.py`. Mergeable, no source
      changes.
- [ ] **PR #2** — MonitorCLT opportunity-detection engine: seven-arbitrage
      taxonomy, signals, valuation, end-to-end pipeline
      (`claude/progress-to-100-3ag1x7`). 15 tool modules and a 41-item
      roadmap — see section 10.
- [ ] **PR #3** — Production-readiness audit report (`AUDIT.md`)
      (`claude/monitorclt-full-audit-7qf9y5`). Landing this first makes
      sections 2–5 below reviewable in-repo instead of on a branch.
- [ ] **PR #5** — BEA API client with CLI interface
      (`claude/bea-gov-api-monitorclt-l7spma`).
- [ ] **PR #6** — User notifications via SendGrid email and Twilio SMS
      (`claude/twilio-sendgrid-setup-swieir`).
- [ ] **PR #7** — Interactive map of parcels adjacent to Mecklenburg County
      land (`claude/mecklenburg-adjacent-parcels-gpatwp`).
- [ ] **PR #10** — Agent-operations layer spec for MonitorCLT
      (`claude/grockbot-monitorclt-integration-3sk79p`). CI is currently red
      on this branch — fix before merging. Rollout phases in section 9.

## 2. Correctness — wrong numbers in tax output

Highest-risk band in the audit: these produce incorrect figures in a filed tax
document, some of them silently.

- [ ] JP tax report generates year sheets in dict-insertion order and carries
      over from `year - 1` unconditionally, so gap years give `#REF!` and
      out-of-order years give silently wrong average-unit-price carry-over.
      Iterate sorted years and carry from the last generated year.
      (`src/rp2/plugin/report/jp/tax_report_jp.py:177-190, 347-352`)
- [ ] JP date-filter guard uses `and` where it needs `or`, so passing only
      `--from_date` or only `--to_date` bypasses rejection and omits
      pre-filter history from the moving average. One-character fix.
      (`tax_report_jp.py:98-99`)
- [ ] US long-term capital gains uses `(sale - buy).days >= 365`, which
      misclassifies exact-one-year and leap-year-spanning sales and varies
      with time of day. Compare calendar dates, have country plugins return a
      rule rather than a day count, and add 365/366-day boundary tests.
      (`src/rp2/gain_loss.py:203`, `src/rp2/plugin/country/us.py:29`)
- [ ] ODS parser probes the row after a table marker inside
      `except Exception: pass`, so a first data row with any malformed field
      is silently treated as a header and dropped from the computation.
      Positively match header keywords; log every skipped row.
      (`src/rp2/ods_parser.py:112-124`)
- [ ] Accounting methods from the `[accounting_methods]` config section skip
      the country check that `-m` gets, so a JP config can run LIFO although
      Japan permits only FIFO. Validate against the country's allowed set
      before `import_module`. (`src/rp2/rp2_main.py:107-118`)
- [ ] Full report hardcodes "USD" on the Summary sheet even for JPY/EUR
      countries. (`rp2_full_report.py:215-216`)
- [ ] `if crypto_in_running_sum is not ZERO` is an identity check against the
      singleton; any arithmetic result that is numerically zero falls through
      to a raw `decimal.DivisionByZero`. Use `> ZERO`.
      (`src/rp2/computed_data.py:132`)
- [ ] `KeyError: 1970` crash on single-entry accounting-method configs.
      (`abstract_ods_generator.py:55,88`)
- [ ] `RP2Decimal` defines a quantized `__eq__` with no `__hash__`, so
      `hash(RP2Decimal("1"))` raises and the frozen `eq` dataclasses holding
      one (`balance.py:35`, `computed_data.py:105`) explode on first set/dict
      use. Tolerance equality can't be hashed consistently — keep exact
      `Decimal` equality and expose `approx_eq()` for the engine.
      (`src/rp2/rp2_decimal.py:36-42`)
- [ ] Comparisons quantize under `prec=31`, so `RP2Decimal("1e40") > ZERO`
      raises raw `decimal.InvalidOperation` — plausible for meme-token unit
      balances. Re-raise as `RP2ValueError` or compare by subtraction.
      (`rp2_decimal.py:33-58`)
- [ ] Decimal context is mutated on the importing thread at class-body
      execution, so other threads get `prec=28` with no float trap and
      embedding apps are side-effected. Use a dedicated `Context` with
      `localcontext()` around `compute_tax`. (`rp2_decimal.py:29-30`)
- [ ] Negative/zero `crypto_in` is allowed for STAKING, but STAKING is
      earn-typed and therefore always taxable, so the engine rejects it — the
      documented feature is a guaranteed crash with no test coverage. Reject
      at construction or handle it in the engine.
      (`src/rp2/in_transaction.py:57-62`)
- [ ] Dust amounts: `non_zero=True` validation uses the 1e-13-tolerant `==`,
      so a dust taxable fraction is taxable per `is_taxable()` yet rejected as
      "zero value", killing the run. Align the taxability check and validator
      on one quantization. (`gain_loss.py:40`)
- [ ] Yearly summaries filter by `year >= from_date.year` while the detailed
      set filters by full date, so a mid-year `from_date` makes the two report
      views disagree. (`computed_data.py:135-136, 216`)
- [ ] Decide, document, and enforce a timezone policy for tax-year
      attribution.

## 3. Dead and broken features

- [ ] `generators` config option is dead: the membership test checks
      ConfigParser *section* names, not keys of `[general]`, so the field is
      silently ignored — and since `-l/--plugin` hard-errors pointing at it,
      there is no working way to select report generators. Fix the test, add a
      test case, and decide short vs fully-qualified plugin names.
      (`src/rp2/configuration.py:181`)
- [ ] Transaction-type localization is permanently broken: `entry_types` binds
      `_` at import time and builds its translation map at module load, so
      `-g es` still returns `"buy"`. Resolve `_` at call time, build the map
      lazily, and add a non-English golden test.
      (`src/rp2/localization.py:24-41`, `src/rp2/entry_types.py:19, 73-87`)
- [ ] `rp2_config` migration tool crashes on schema-valid input containing
      `generators`.

## 4. Error UX and documentation

- [ ] Catch `RP2Error` separately at the top level and print it in one or two
      lines, with the traceback going only to the log file. This alone fixes
      most of the "cryptic error" experience. (`rp2_main.py:170-172`)
- [ ] Add remediation hints to the most-hit errors: unknown exchange/holder →
      name the config list to add it to; invalid transaction type → list valid
      values; negative balance (`balance.py:172`) → mention missing IN/INTRA
      transfers and `-n`; accounting-method year gap
      (`accounting_engine.py:152`) → explain the `[accounting_methods]` remedy
      instead of saying "Internal error".
- [ ] Identify the offending row/column in value errors.
- [ ] Add a `--check` dry-run that validates config + ODS and reports *all*
      errors at once instead of dying on the first.
- [ ] Fix `-m` help text to state the real default (country default, not
      `''`), and make `-g` list available languages.
      (`rp2_main.py:306-314`)
- [ ] Replace `rp2_generic`'s magic `CURRENCY_CODE` /
      `LONG_TERM_CAPITAL_GAINS` env vars with CLI flags, and fix the invalid
      Windows syntax in `docs/supported_countries.md:47-50`.
- [ ] Stop dumping the full `--help` after a path error; check existence
      before extension so `input.xlsx` gets the right message.
      (`rp2_main.py:373-395`)
- [ ] Warn at generation time that output is ODS and Excel support is limited;
      link the FAQ entry.
- [ ] Read config files as `utf-8-sig` so a BOM isn't a traceback; wrap
      configparser errors.
- [ ] Reject partial dates from dateutil rather than silently completing them;
      detect a corrupt ODS instead of reporting "sheet BTC does not exist".
- [ ] Doc corrections: `docs/user_faq.md:239` says `DONATION` but the valid
      value is `DONATE`; `docs/input_files.md:115-166` shows quoted config
      values that ConfigParser takes literally and omits `unique_id` from all
      three header sections; `README.md:174` omits LOFO; `setup.cfg:5`
      description omits LOFO and misspells "menthods"; no CHANGELOG entry for
      LOFO; four broken intra-doc anchors (README→FAQ fee link, `user_faq.md:139`,
      `output_files.md:81`, `supported_countries.md:24`).
- [ ] Message wording: "positive integer was expected" for column 0 (0 is
      valid — say non-negative, `configuration.py:249-251`); JP error omitting
      IntraTransaction (`tax_report_jp.py:310`); `transaction_set.py:34`
      hardcodes "IN transaction set" for every set type; stray quote in
      `abstract_ods_generator.py:103`.

## 5. Security, CI, and packaging

- [ ] Spreadsheet formula injection: any string cell starting with `=` is
      written as a live formula, and `notes` / `unique_id` / exchange / holder
      / asset names are user-controlled (often imported from exchange CSVs).
      Write formulas only through an explicit formula API and escape quotes in
      all interpolations. (`abstract_ods_generator.py:173-185`,
      `rp2_full_report.py:786-795`, `open_positions.py:386`)
- [ ] CodeQL scanning has been dead for years — the workflow still uses
      `codeql-action/*@v1` (hard-deprecated January 2023) and
      `actions/checkout@v2`, so the repo only looks scanned. Upgrade to v3/v4,
      SHA-pinned. (`.github/workflows/codeql-analysis.yml:42-71`)
- [ ] Log privacy: financial debug logs are world-readable. Create log files
      `0o600`, use `delay=True`, warn about DEBUG content, validate
      `LOG_LEVEL`.
- [ ] Packaging: `python_requires`, `include_package_data`, and `zip_safe` sit
      under `[options.packages.find]` instead of `[options]`, so published
      metadata carries no `Requires-Python` and pip will install on an ancient
      interpreter. (`setup.cfg:61-65`)
- [ ] Modernize CI: Python 3.10–3.14 matrix (currently tests two EOL
      versions and misses 3.13/3.14), SHA-pinned actions, `permissions`
      blocks, concurrency groups, pip cache, Dependabot.
- [ ] Build with `python -m build`; pin dev extras.
- [ ] Migrate to PEP 621 `pyproject.toml`; add a release workflow using
      Trusted Publishing; keep pre-commit hooks up to date.

## 6. Structural debt

- [ ] Extract a shared US/IE report base class with a `generate()` template
      method; move class-level mutable state into `__init__` in
      `rp2_full_report` and `open_positions`.
- [ ] Add a template-contract validator — a per-plugin manifest of sheets,
      anchors and styles checked at init — turning silent template failures
      into fast errors, then de-magic the JP anchors.
- [ ] Make `row` mandatory on `AbstractTransaction`: it currently defaults to
      `id(self)`, which becomes a nondeterministic `internal_id` (unstable
      ordering and AVL keys), and `__eq__` compares `internal_id` with no
      class check, so an `InTransaction` can equal an `OutTransaction`.
      (`src/rp2/abstract_transaction.py:42` — the in-code `TODO`)
- [ ] Handle the ODS-parser row that "could still be a transaction but with
      some bad fields" rather than dropping it. (`src/rp2/ods_parser.py:118` —
      the in-code `TODO`)
- [ ] Hoist duplicated `to_string` scaffolding from the four transaction
      classes into `AbstractTransaction`. (`gain_loss.py:97-122`,
      `in_transaction.py:123-149`, `out_transaction.py:125-149`,
      `intra_transaction.py:76-104`)
- [ ] Unify config validation: require header columns on the INI path, add
      schema `required`/anchoring, fix `generators` in the JSON translator.
- [ ] Remove dead code: four grand totals accumulated and never read, a
      redundant `list(sorted(...))`, and a stale "frozen and eq are not set"
      comment sitting above `@dataclass(frozen=True, eq=True)`.
      (`computed_data.py:104-105, 168-171, 184-189`)

## 7. Test debt and performance

- [ ] Direct unit tests for `accounting_engine` (all four methods,
      same-timestamp disambiguation, exhaustion paths),
      `abstract_accounting_method`, LOFO sort semantics, and
      `rp2_configuration_translator`.
- [ ] Make `ods_diff` decimal-safe.
- [ ] Add pytest markers and xdist, and a coverage gate — the suite currently
      takes 10–20 minutes.
- [ ] Optimize the `_fill_cell` hot path; bisect for filtered iteration;
      early-exit in the parser.

## 8. MonitorCLT — probate cross-reference (on `main`)

The only MonitorCLT code merged to `main`. Follow-ups named at the end of
`tools/monitorclt_probate/README.md`, none of them done.

- [ ] Have a North Carolina real-estate/probate attorney review the workflow
      before operationalizing it.
- [ ] Keep solicitation of estates within NC rules on contacting personal
      representatives — human verification stays between a confirmed match and
      any contact.
- [ ] Hand-label real Mecklenburg estate/parcel pairs and re-run
      `evaluate.py`: the weights in `match_rules.json` are defensible starting
      points, not measured ones, and the 100% precision / 50% recall figures
      describe the synthetic sample only. Label the hard cases — common
      surnames, remarriages, junior/senior pairs.

## 9. MonitorCLT — agent operations layer (PR #10)

Specs plus `intake/adapt.py` and `load_run.py`. The rollout is six phases with
a gate on each; phase 0 is the PR itself, so phases 1–5 are all outstanding.
Gates are quoted from `tools/monitorclt_probate/agents/README.md`.

- [ ] Fix CI on the branch — currently red, blocking the merge.
- [ ] **Phase 0** — agree the roster; load `AGENTS.md` into shared memory.
- [ ] **Phase 1** — intake for one county, one source (estate cases),
      human-triggered. Gate: two weeks, and a human spot-checks 20 records
      against the source finding zero fabricated or dropped fields.
- [ ] **Phase 2** — the runner. Gate: a loaded run is identical to a hand-run
      of `crossref.py`, and the confirmed-row `CHECK` is settled and
      implemented in `load_run.py`.
- [ ] **Phase 3** — queue triage. Gate: the reviewer confirms the packet saved
      time and never nudged toward a confirm.
- [ ] **Phase 4** — calibration proposals. Gate: a proposal is adopted only
      after `evaluate.py` holds target precision on a label set that *grew*
      since the last change (a static label set is tuning to the test).
- [ ] **Phase 5** — outreach drafts. Gate: NC attorney sign-off on both
      template and process. No routine triggers outreach by construction.
- [ ] Give the two orphaned run outputs their owners:
      `unmatched_estate_parcels` → intake's backlog, `skipped_estates` →
      calibration as a name-parsing problem no threshold reaches.
- [ ] Hold the four known risks as review criteria, not one-time notes:
      no browser-scraping at volume, no coordinator that digests the queue,
      no PII in shareable artifacts, no calibrating against a static label set.

## 10. MonitorCLT — platform roadmap (PR #2)

`MONITORCLT_PLATFORM_ROADMAP.md` on that branch carries 41 unchecked items
across 15 tool modules. **It stays the source of truth** — once PR #2 lands,
work the roadmap and keep this section as a pointer, not a copy. Two things
worth carrying here because they gate everything else:

- [ ] **Phase 0 is the sequencing rule.** Nothing in phases 2–5 ships before
      Phase 0 is green: 7 pipelines below 100% (three dead-lettered),
      `contactable_pct` at 15% against a 50% target, message-queue backlog
      growing, disk at 88%. Exit criterion is `pipeline.success_rate_7d` at
      100% across the board for seven consecutive days. Distributing broken
      lead flow to partners burns the only asset that makes the model work.
- [ ] **Most of this work isn't in this repo.** The roadmap notes it lives in
      rp2 for persistence only; implementation happens in the MonitorCLT
      codebase, which is not on GitHub. Either run Claude Code on the
      MonitorCLT host or push that codebase to a repo this account can attach
      — until then these items can be tracked here but not worked here.

Outstanding groups, so the shape is visible without opening the branch:

- [ ] **Sourcing / provenance** — apply `schema.sql` and backfill provenance;
      run the remaining 10 sources on the host and upsert into `signals`; emit
      `source_coverage` into the daily digest (a source dropping to
      `coverage_coming` is the alert dead-lettering never gave); convert the
      dead-lettered scrapers (`rod_lending_ocr`/`rod_match`,
      `iredell_delinquent` — needs a spatial-join adapter); St. Louis permits
      (Accela App ID) and tax sale (Cloudflare — browser_api on the host or
      ask the Collector); register each source as a nightly pipeline with its
      own success metric and freshness gate.
- [ ] **Scoring** — wire `load_*` to Postgres and write a `leads` table; emit
      `score.leads_priority_plus` and band counts to the digest; build the ROD
      lien job on the host (browser-only in all 8 counties; start with Gaston
      CCS, no Cloudflare); add entity-resolution depth once ROD deed data is
      wired; tune weights against real closed-deal outcomes.
- [ ] **Contact enrichment** — load Regrid county exports into a `parcels`
      table; replace CSV I/O with Postgres; run against the full contact base;
      register a nightly `contact_enrichment` pipeline with digest metrics;
      bake off BatchData / PropertyReach / Datafinder for skip trace; start
      direct mail to absentee owners, which needs no skip trace and generates
      the inbound that creates SMS consent.
- [ ] **Phone / SMS activation** — the slow track: LLC + EIN, privacy policy
      and SMS terms page, Twilio account and 704/980 numbers, A2P 10DLC brand
      and campaign registration (1–3 weeks of carrier review), messaging
      service with advanced opt-out. Then the build: `sms_sender` worker gated
      on opt-in/DNC and quiet hours, inbound and delivery webhooks, STOP-reply
      sync to a DB opt-out flag (Twilio-side blocking is not enough), inbound
      voice with transcription, four `sms.*` metrics, end-to-end test before
      approval lands. Then go-live: DNC and litigator scrub before any send,
      warm-up ramp, no link shorteners.
- [ ] **Entity resolution** — parcels → beneficial owner (LLC/person) so
      portfolio and repeat-seller signals surface. Not built; named as the
      edge neither competitor has.
- [ ] **Cheap derived signals still to build** — `assemblage_adjacency_value`;
      compute `neighbors`/`road_frontage` from real parcel polygons on the
      host; `tax_lot_legal_lot_mismatch` and `address_anomaly_multiunit`; feed
      real recorded dates into the catalyst calendar and resolution events
      into the lifecycle engine as ROD/planning/tax sources come online.
- [ ] **Calibrate the valuation constants** — adjustment coefficients ship as
      Charlotte-SFR placeholders. The method is sound, the constants are not
      yours yet; calibrate per submarket before trusting the dollar figures.
- [ ] **Phases 1–5** (deal core, partner layer, buyer/dispo, intelligence
      pages, funnel) — see the roadmap. Gated behind Phase 0.

## 11. MonitorCLT — other open branches

- [ ] **PR #5** adds `src/monitorclt/` (a BEA API client with a CLI) inside
      the rp2 source tree. Decide whether MonitorCLT code belongs under
      `src/` alongside the tax calculator, or in `tools/` with the other
      MonitorCLT modules, before this sets a precedent.
- [ ] **PR #6** adds `docs/user_notifications.md` for SendGrid/Twilio. Check
      it against the A2P 10DLC and consent requirements in section 10 — a
      notifications doc that predates carrier registration will be wrong about
      what can actually be sent.
- [ ] **PR #7** adds `mecklenburg_adjacent_parcels/` (fetch, classify,
      analyze, and a generated map) at the repository root — same placement
      question as PR #5, and it overlaps the adjacency work in
      `monitorclt_geometry` on PR #2. Reconcile the two rather than merging
      both.

## 12. Housekeeping

- [ ] Sync the fork with upstream `eprbell/rp2` v1.7.2+ (adds the LOST
      transaction type) and decide the fork's purpose: contribution branch or
      long-lived divergence.
- [ ] Verify the ES long-term holding period with a tax professional; resolve
      the JP accounting-method placeholder.
- [ ] Restructure the docs DaLI-first — hand-entering every transaction and
      hand-looking-up historical prices is the worst stage of the user
      journey — and design a price plugin.
- [ ] The `second-brain/` vault is still empty: `wiki/index.md` has no entries
      and `log.md` only has the init line. Ingest a first source or drop the
      vault.
- [ ] Decide what this repository is. It is an upstream tax-calculator fork
      carrying an unrelated real-estate platform across seven branches that
      never merge. Either split MonitorCLT into its own repository or accept
      the mixture deliberately and say so in the README.
