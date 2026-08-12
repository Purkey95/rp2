# Copyright 2026 Purkey95
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

"""Command-line interface for the BEA API client.

Examples (requires BEA_API_KEY in the environment):
    python -m monitorclt datasets
    python -m monitorclt parameters NIPA
    python -m monitorclt values NIPA TableName
    python -m monitorclt data NIPA TableName=T10101 Frequency=Q Year=2025
"""

import argparse
import json
import sys
from typing import Any, Dict, List

from monitorclt.bea_client import BeaApiClient, BeaApiError


def _parse_key_value_args(pairs: List[str]) -> Dict[str, str]:
    parameters: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"Invalid parameter '{pair}': expected Name=Value")
        name, _, value = pair.partition("=")
        parameters[name] = value
    return parameters


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="monitorclt", description="Query the BEA (bea.gov) data API")
    parser.add_argument("--api-key", help="BEA API key (defaults to the BEA_API_KEY environment variable)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("datasets", help="List available BEA datasets")

    parameters_parser = subparsers.add_parser("parameters", help="List parameters of a dataset")
    parameters_parser.add_argument("dataset", help="Dataset name (e.g. NIPA, GDPbyIndustry, Regional)")

    values_parser = subparsers.add_parser("values", help="List valid values of a dataset parameter")
    values_parser.add_argument("dataset", help="Dataset name")
    values_parser.add_argument("parameter", help="Parameter name (e.g. TableName)")

    data_parser = subparsers.add_parser("data", help="Fetch data from a dataset")
    data_parser.add_argument("dataset", help="Dataset name")
    data_parser.add_argument("parameters", nargs="*", help="Dataset parameters as Name=Value (e.g. TableName=T10101 Frequency=Q Year=2025)")

    args: argparse.Namespace = parser.parse_args()

    try:
        client: BeaApiClient = BeaApiClient(api_key=args.api_key)
        result: Any
        if args.command == "datasets":
            result = client.get_dataset_list()
        elif args.command == "parameters":
            result = client.get_parameter_list(args.dataset)
        elif args.command == "values":
            result = client.get_parameter_values(args.dataset, args.parameter)
        else:
            result = client.get_data(args.dataset, **_parse_key_value_args(args.parameters))
    except BeaApiError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
