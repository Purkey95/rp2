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

import re
import unittest
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from typing import List, Optional

CONFIG_PATH: Path = Path("config")
INPUT_PATH: Path = Path("input")


class TestGeneratorsConfiguration(unittest.TestCase):
    """The optional 'generators' key of the 'general' section selects which report
    generator plugins run (see docs/input_files.md). It is honored on every path:
    omitted, naming a country-neutral generator, or naming a country-specific one.
    """

    @staticmethod
    def _run_with(generators: Optional[str], directory: str) -> List[str]:
        source = (CONFIG_PATH / Path("test_data.ini")).read_text(encoding="utf-8")
        if generators is not None:
            source = re.sub(r"(\[general\][^\[]*)", r"\1generators = " + generators + "\n", source, count=1)
        config_path = Path(directory) / Path("generators_test.ini")
        config_path.write_text(source, encoding="utf-8")
        output_dir = Path(directory) / Path("output")
        output_dir.mkdir()
        # -n: the shared test input intentionally drives a balance negative.
        run(
            ["rp2_us", "-n", "-o", str(output_dir), str(config_path), str(INPUT_PATH / Path("test_data.ods"))],
            check=True,
            capture_output=True,
        )
        return sorted(path.name for path in output_dir.iterdir())

    def test_omitted_generators_uses_the_country_default_set(self) -> None:
        expected: List[str] = ["fifo_open_positions.ods", "fifo_rp2_full_report.ods", "fifo_tax_report_us.ods"]
        with TemporaryDirectory() as directory:
            self.assertEqual(self._run_with(None, directory), expected)

    def test_generators_selects_only_the_named_generator(self) -> None:
        expected: List[str] = ["fifo_open_positions.ods"]
        with TemporaryDirectory() as directory:
            self.assertEqual(self._run_with("open_positions", directory), expected)

    def test_generators_accepts_a_country_specific_generator(self) -> None:
        expected: List[str] = ["fifo_open_positions.ods", "fifo_tax_report_us.ods"]
        with TemporaryDirectory() as directory:
            self.assertEqual(self._run_with("open_positions, us.tax_report_us", directory), expected)


if __name__ == "__main__":
    unittest.main()
