# Copyright 2026 purkey95
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Verify that RP2's documentation matches the plugins that actually exist in the code.

Checks (code -> docs direction):
  * every country plugin in src/rp2/plugin/country/ has a rp2_<country> console script in
    setup.cfg and is mentioned in docs/supported_countries.md;
  * every accounting-method plugin in src/rp2/plugin/accounting_method/ is mentioned in
    docs/supported_countries.md;
  * every report generator in src/rp2/plugin/report/ (top level and country subpackages)
    is mentioned in docs/supported_countries.md and docs/output_files.md.

Standard library only, so it runs in CI without installing the package. Exit code is
non-zero if any check fails.
"""

import configparser
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "src" / "rp2" / "plugin"
SUPPORTED_COUNTRIES_DOC = REPO_ROOT / "docs" / "supported_countries.md"
OUTPUT_FILES_DOC = REPO_ROOT / "docs" / "output_files.md"
SETUP_CFG = REPO_ROOT / "setup.cfg"

ABSTRACT_PREFIX = "abstract_"


def _plugin_module_names(directory: Path) -> List[str]:
    return sorted(
        path.stem for path in directory.glob("*.py") if path.stem != "__init__" and not path.stem.startswith(ABSTRACT_PREFIX)
    )


def _console_scripts() -> List[str]:
    parser = configparser.ConfigParser()
    parser.read(SETUP_CFG)
    entry_points = parser.get("options.entry_points", "console_scripts", fallback="")
    return [line.split("=")[0].strip() for line in entry_points.splitlines() if "=" in line]


def main() -> int:
    errors: List[str] = []
    supported_countries_text = SUPPORTED_COUNTRIES_DOC.read_text(encoding="utf-8").lower()
    output_files_text = OUTPUT_FILES_DOC.read_text(encoding="utf-8").lower()
    console_scripts = _console_scripts()

    countries = _plugin_module_names(PLUGIN_ROOT / "country")
    for country in countries:
        executable = f"rp2_{country}"
        if executable not in console_scripts:
            errors.append(f"country plugin '{country}' has no '{executable}' console script in setup.cfg")
        if executable not in supported_countries_text:
            errors.append(f"country plugin '{country}' ('{executable}') is not documented in {SUPPORTED_COUNTRIES_DOC.name}")

    accounting_methods = _plugin_module_names(PLUGIN_ROOT / "accounting_method")
    for method in accounting_methods:
        if method not in supported_countries_text:
            errors.append(f"accounting-method plugin '{method}' is not documented in {SUPPORTED_COUNTRIES_DOC.name}")

    report_directory = PLUGIN_ROOT / "report"
    generators = _plugin_module_names(report_directory)
    for subpackage in sorted(path for path in report_directory.iterdir() if path.is_dir() and (path / "__init__.py").exists()):
        generators.extend(_plugin_module_names(subpackage))
    for generator in generators:
        if generator not in supported_countries_text:
            errors.append(f"report generator '{generator}' is not documented in {SUPPORTED_COUNTRIES_DOC.name}")
        if generator not in output_files_text:
            errors.append(f"report generator '{generator}' is not documented in {OUTPUT_FILES_DOC.name}")

    print(f"Checked {len(countries)} countries, {len(accounting_methods)} accounting methods, {len(generators)} report generators.")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Documentation is in sync with plugins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
