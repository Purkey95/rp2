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

"""Client for the U.S. Bureau of Economic Analysis (BEA) data API.

API documentation: https://apps.bea.gov/api/bea_web_service_api_user_guide.htm

The API key (called "UserID" by BEA) is read from the BEA_API_KEY environment
variable unless passed explicitly. Register for a free key at
https://apps.bea.gov/api/signup/
"""

import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEA_API_BASE_URL: str = "https://apps.bea.gov/api/data"
BEA_API_KEY_ENV_VAR: str = "BEA_API_KEY"
_DEFAULT_TIMEOUT_SEC: float = 30.0
_USER_AGENT: str = "monitorclt/0.1"


class BeaApiError(Exception):
    """Raised when the BEA API returns an error or an unexpected payload."""


class BeaApiClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = _DEFAULT_TIMEOUT_SEC) -> None:
        resolved_key: Optional[str] = api_key or os.environ.get(BEA_API_KEY_ENV_VAR)
        if not resolved_key:
            raise BeaApiError(f"No BEA API key: pass api_key or set the {BEA_API_KEY_ENV_VAR} environment variable")
        self.__api_key: str = resolved_key
        self.__timeout: float = timeout

    def _request(self, method: str, **params: str) -> Dict[str, Any]:
        query: Dict[str, str] = {
            "UserID": self.__api_key,
            "method": method,
            "ResultFormat": "JSON",
        }
        query.update(params)
        url: str = f"{BEA_API_BASE_URL}?{urlencode(query)}"
        if not url.startswith("https://"):
            raise BeaApiError(f"Refusing to open non-HTTPS URL: {url}")
        request: Request = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=self.__timeout) as response:  # nosec B310 - HTTPS-only, enforced above
            payload: Any = json.loads(response.read().decode("utf-8"))

        try:
            result: Dict[str, Any] = payload["BEAAPI"]["Results"]
        except (KeyError, TypeError) as exc:
            raise BeaApiError(f"Unexpected BEA API payload: {payload!r}") from exc
        # The API reports request-level errors inside Results.Error (and always with HTTP 200).
        error: Any = result.get("Error") if isinstance(result, dict) else None
        if error:
            raise BeaApiError(f"BEA API error: {error}")
        return result

    def get_dataset_list(self) -> List[Dict[str, str]]:
        return list(self._request("GETDATASETLIST")["Dataset"])

    def get_parameter_list(self, dataset_name: str) -> List[Dict[str, str]]:
        return list(self._request("GETPARAMETERLIST", datasetname=dataset_name)["Parameter"])

    def get_parameter_values(self, dataset_name: str, parameter_name: str) -> List[Dict[str, str]]:
        return list(self._request("GETPARAMETERVALUES", datasetname=dataset_name, ParameterName=parameter_name)["ParamValue"])

    def get_data(self, dataset_name: str, **parameters: str) -> Dict[str, Any]:
        return self._request("GETDATA", datasetname=dataset_name, **parameters)
