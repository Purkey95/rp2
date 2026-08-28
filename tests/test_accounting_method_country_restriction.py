# Copyright 2021 eprbell
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

import unittest
from pathlib import Path
from subprocess import CompletedProcess, run
from tempfile import TemporaryDirectory

from rp2.plugin.country.jp import JP
from rp2.plugin.country.us import US

CONFIG_PATH: Path = Path("config")
INPUT_PATH: Path = Path("input")


class TestAccountingMethodCountryRestriction(unittest.TestCase):
    """A country's set of legal accounting methods must be enforced on every path.

    The -m command line option is constrained by argparse choices, but the
    'accounting_methods' configuration file section was not checked against the
    country plugin, so a config file could select a method the country does not
    allow and the report would be computed with it.
    """

    @staticmethod
    def _config_selecting(method: str, directory: str) -> Path:
        # Derive a config from the standard test config by adding an accounting_methods
        # section, so only the method under test differs from a known-good configuration.
        source = (CONFIG_PATH / Path("test_data.ini")).read_text(encoding="utf-8")
        self_path = Path(directory) / Path("accounting_method_test.ini")
        self_path.write_text(source.replace("[in_header]", f"[accounting_methods]\n2020 = {method}\n\n[in_header]", 1), encoding="utf-8")
        return self_path

    def _run(self, country: str, method: str) -> "CompletedProcess[str]":
        with TemporaryDirectory() as directory:
            config_path = self._config_selecting(method, directory)
            return run(
                [f"rp2_{country}", "-o", directory, str(config_path), str(INPUT_PATH / Path("test_data.ods"))],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_country_illegal_method_in_config_is_rejected(self) -> None:
        # Japan allows fifo only.
        self.assertNotIn("lifo", JP().get_accounting_methods())
        result = self._run("jp", "lifo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed in country 'jp'", result.stderr + result.stdout)

    def test_country_legal_method_in_config_is_accepted(self) -> None:
        # The United States allows lifo, so the same config shape must be accepted.
        self.assertIn("lifo", US().get_accounting_methods())
        result = self._run("us", "lifo")
        self.assertNotIn("not allowed in country", result.stderr + result.stdout)

    def test_country_default_method_in_config_is_accepted(self) -> None:
        result = self._run("jp", JP().get_default_accounting_method())
        self.assertNotIn("not allowed in country", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
