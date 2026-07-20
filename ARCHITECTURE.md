<!--- Copyright 2026 purkey95 --->

<!--- Licensed under the Apache License, Version 2.0 (the "License"); --->
<!--- you may not use this file except in compliance with the License. --->
<!--- You may obtain a copy of the License at --->

<!---     http://www.apache.org/licenses/LICENSE-2.0 --->

<!--- Unless required by applicable law or agreed to in writing, software --->
<!--- distributed under the License is distributed on an "AS IS" BASIS, --->
<!--- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. --->
<!--- See the License for the specific language governing permissions and --->
<!--- limitations under the License. --->

# RP2 Architecture

RP2 is a privacy-focused, offline crypto tax calculator: everything runs locally, nothing is
sent to any server. This document maps the codebase for contributors. For plugin-authoring
details see [README.dev.md](README.dev.md); for user-facing behavior see
[docs/input_files.md](docs/input_files.md) and [docs/output_files.md](docs/output_files.md).

## Table of Contents
* **[Data Flow](#data-flow)**
* **[Directory Ownership](#directory-ownership)**
* **[Core Engine Modules](#core-engine-modules)**
* **[Plugin System](#plugin-system)**
* **[Testing Strategy](#testing-strategy)**
* **[Keeping This Document Honest](#keeping-this-document-honest)**

## Data Flow

```
 .ini config file            ODS input spreadsheet
 (column mapping,            (IN / OUT / INTRA
  assets, exchanges,          transactions)
  holders, methods)                 │
        │                           │
        ▼                           ▼
  configuration.py  ─────────▶ ods_parser.py ──▶ input_data.py (InputData)
  (jsonschema-validated                              │
   via configuration_schema.py)                      ▼
                                              tax_engine.py (compute_tax)
                                                     │  uses accounting_engine.py
                                                     │  (per-year accounting methods)
                                                     ▼
                                              computed_data.py (ComputedData:
                                              gain_loss_set, balances, ...)
                                                     │
                                                     ▼
                                     report generator plugins (plugin/report/)
                                     render ODS templates from plugin/report/data/
                                                     │
                                                     ▼
                                          output/ *.ods reports + log/
```

The whole pipeline is orchestrated by `src/rp2/rp2_main.py`. Each country plugin defines a
console entry point (e.g. `rp2_us`, declared in `setup.cfg`) that calls
`rp2_main(<CountryInstance>())`.

## Directory Ownership

Each directory has one job; changes should land in the directory that owns the concern.

| Path | Owns |
|---|---|
| `src/rp2/` | Core engine: models, parsing, tax computation, precision, i18n, logging |
| `src/rp2/plugin/country/` | Country plugins (subclasses of `AbstractCountry`) |
| `src/rp2/plugin/accounting_method/` | Accounting-method plugins (FIFO/LIFO/HIFO/LOFO) |
| `src/rp2/plugin/report/` | Report generators + country subpackages + ODS templates (`data/`) |
| `src/rp2/locales/` | gettext translation catalogs |
| `config/` | Example and test `.ini` configuration files |
| `input/` | Example and test ODS input files; `input/golden/` holds golden outputs |
| `tests/` | Pytest suite (unit tests + golden-file ODS diffs) |
| `docs/` | User and developer documentation |
| `scripts/` | Repo maintenance/CI helper scripts (standard-library only) |

## Core Engine Modules

All under `src/rp2/`:

* **Entry / orchestration**: `rp2_main.py` (argument parsing, pipeline wiring),
  `rp2_configuration_translator.py` (the `rp2_config` tool).
* **Configuration**: `configuration.py` validated against `configuration_schema.py`
  (jsonschema).
* **Input model**: `ods_parser.py` reads the spreadsheet into `input_data.py`. Transactions
  are modeled by `abstract_transaction.py` and its three concrete kinds —
  `in_transaction.py` (acquisitions), `out_transaction.py` (disposals),
  `intra_transaction.py` (transfers between the user's own accounts) — collected in
  `transaction_set.py`. Taxable-event categories live in `entry_types.py`.
* **Computation**: `tax_engine.py` (`compute_tax`) pairs acquired lots with disposals using
  `accounting_engine.py`, which holds a year → accounting-method mapping (methods can change
  per year). Results: `gain_loss.py` / `gain_loss_set.py`, `balance.py`, aggregated in
  `computed_data.py`.
* **Precision**: `rp2_decimal.py` — high-precision decimal arithmetic (requires the system
  `mpdecimal` library). Never use floats for crypto/fiat amounts.
* **Support**: `rp2_error.py` (all errors are `RP2RuntimeError`/`RP2TypeError`/
  `RP2ValueError` subclasses), `logger.py`, `localization.py` (Babel/gettext).

## Plugin System

RP2 has three plugin types, discovered and selected at run time:

1. **Countries** (`src/rp2/plugin/country/`): subclass `AbstractCountry`
   (`src/rp2/abstract_country.py`) and declare the ISO country/currency codes, the long-term
   capital-gains holding period, allowed and default accounting methods, default report
   generators, and default report language. Each has a `rp2_entry()` function registered as a
   `rp2_<country>` console script in `setup.cfg`.
2. **Accounting methods** (`src/rp2/plugin/accounting_method/`): subclass
   `AbstractAccountingMethod` and define lot-selection order. Selected with the `-m` CLI
   option or per-year via the `accounting_methods` section of the `.ini` config.
3. **Report generators** (`src/rp2/plugin/report/`): subclass `AbstractReportGenerator`
   (ODS generators extend `abstract_ods_generator.py` and fill templates from
   `plugin/report/data/<country>/`). Country-agnostic generators (`rp2_full_report`,
   `open_positions`) live at the top level; country-specific ones live in subpackages
   (`us/tax_report_us.py`, `jp/tax_report_jp.py`, `ie/tax_report_ie.py`). Generators are
   discovered dynamically via package iteration and selected by the config `generators`
   section or country defaults.

Adding a plugin of any type also requires updating the user docs
([docs/supported_countries.md](docs/supported_countries.md),
[docs/output_files.md](docs/output_files.md)) — CI enforces this (see below).

## Testing Strategy

* **Unit tests** (`tests/test_*.py`) cover configuration, transaction models, the tax engine,
  and balances.
* **Golden-file tests** (`tests/test_ods_output_diff*.py` with `tests/ods_diff.py`) run the
  full pipeline per country and diff the generated ODS reports against golden copies in
  `input/golden/`. Any intentional change to report output requires regenerating the golden
  files — an unexplained golden diff is a bug.
* CI (`.github/workflows/`) runs the suite on Ubuntu/macOS/Windows across supported Python
  versions, plus mypy/pylint/bandit (`static_analysis.yml`), CodeQL, and documentation checks
  (`documentation_check.yml`).

## Keeping This Document Honest

Facts that can drift (which countries, accounting methods, and report generators exist, and
whether they're documented) are not duplicated here — they are enforced by
[`scripts/check_documentation.py`](scripts/check_documentation.py), which CI runs on every
push and which you can run locally with `make doc_check`. If you add a plugin and forget the
docs, that check fails.
