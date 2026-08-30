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

# AGENTS.md — Working on RP2

Entry point for automated coding agents (and a quick-start for humans). Read
[ARCHITECTURE.md](ARCHITECTURE.md) for the codebase map and
[README.dev.md](README.dev.md) for plugin-authoring details before making changes.

## What This Project Is

RP2 is a privacy-focused, free, open-source (Apache-2.0) crypto tax calculator. It is a
**CLI-only, offline** Python tool: it reads an ODS spreadsheet of transactions plus an `.ini`
config, computes capital gains locally, and writes ODS reports. Do not add network calls,
telemetry, or server components — offline operation is a core design principle, not an
accident.

## Build, Run, Test

Everything goes through the Makefile (it creates a `.venv` virtualenv and installs RP2 in
editable mode with dev dependencies):

```
make                  # create .venv and install -e ".[dev]"
make run              # run rp2_us on the example/test inputs (writes to output/)
make check            # run the full pytest suite
make static_analysis  # mypy + pylint + bandit
make reformat         # isort + black
make doc_check        # verify plugins are documented (scripts/check_documentation.py)
make clean            # remove venv, caches, build artifacts, output/
```

Note: `rp2_decimal.py` requires the system `mpdecimal` library; on some Linux distributions
it must be installed separately.

## Non-Negotiable Conventions

* **Precision**: never use `float` for crypto or fiat amounts — use `RP2Decimal`
  (`src/rp2/rp2_decimal.py`).
* **Errors**: raise RP2 error types from `src/rp2/rp2_error.py`, not bare exceptions. Public
  functions type-check their arguments explicitly (see `AbstractCountry.type_check` for the
  idiom).
* **Typing**: the codebase is fully typed and checked with mypy (`mypy.ini`); keep it that
  way.
* **Formatting**: isort + black (via `make reformat`); pylint config in `.pylintrc`. Match
  the existing style — including the Apache license header block at the top of every source
  and doc file.
* **i18n**: user-visible report strings go through the localization machinery
  (`src/rp2/localization.py`, catalogs in `src/rp2/locales/`).

## Golden-File Test Discipline

The most important tests are the golden-file ODS diffs (`tests/test_ods_output_diff*.py`):
they run the full pipeline and byte-compare generated reports against golden copies in
`input/golden/`.

* If your change is not supposed to alter report output and a golden diff fails, **your
  change has a bug** — do not regenerate the golden files to make the test pass.
* If your change intentionally alters output, regenerate the affected golden files, and say
  so explicitly in the commit message so reviewers can scrutinize the diff.

## Adding a Plugin (Checklist)

For any of the three plugin types (country, accounting method, report generator — see
[ARCHITECTURE.md](ARCHITECTURE.md#plugin-system)):

1. Implement the plugin under the appropriate `src/rp2/plugin/` subdirectory, subclassing the
   relevant abstract base class.
2. For a country: register the `rp2_<country>` console script in `setup.cfg` and add default
   accounting methods / generators / language.
3. For a report generator: add its ODS template under `src/rp2/plugin/report/data/<country>/`.
4. Add tests, including golden files for new report output.
5. Update the docs: [docs/supported_countries.md](docs/supported_countries.md) and (for
   generators) [docs/output_files.md](docs/output_files.md). `make doc_check` (also run in
   CI) fails if a plugin exists in code but is missing from the docs.
6. Run `make check static_analysis doc_check` before committing.

## Review Focus

When reviewing or self-reviewing changes, prioritize in this order:

1. **Numerical correctness** — tax math errors are the worst possible bug in this project;
   check lot pairing, fee handling, and year boundaries.
2. **Golden diffs explained** — every changed golden file must correspond to an intended
   behavior change.
3. **Privacy** — no new network I/O, no data leaving the machine.
4. **Cross-platform** — CI runs Ubuntu, macOS, and Windows; avoid platform-specific paths or
   shell assumptions.
5. **Docs in sync** — user-facing behavior changes must update `docs/`.
