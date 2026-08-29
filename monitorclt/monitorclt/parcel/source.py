"""Client for the Mecklenburg County CAMA ownership feature layer.

Standard library only, so the loader runs anywhere without a dependency
install. Paging uses ``resultOffset``/``resultRecordCount`` ordered by
``objectid``: ArcGIS does not guarantee a stable page order without an explicit
``orderByFields``, so omitting it silently duplicates and drops records across
pages on a large layer.

Access terms for this service have **not** been verified — see
``second-brain/wiki/distressed-property-outreach-compliance.md``. Confirm
before running a full 428k-record extract on a schedule.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

MECKLENBURG_CAMA_LAYER = (
    "https://meckgis.mecklenburgcountync.gov/server/rest/services"
    "/TaxParcel_Camaownershipvalues/MapServer/0"
)

#: Fields the enrichment pipeline reads. Requesting these explicitly rather
#: than ``*`` keeps the payload stable if the county adds columns.
CAMA_FIELDS: Sequence[str] = (
    "camapid", "pid", "propertyid", "full_owner_name", "nme_ownerlastname",
    "nme_ownerfirstname", "secownerlastname", "secownerfirstname",
    "txt_mailaddr1", "txt_city", "txt_state", "txt_zipcode", "amt_landvalue",
    "amt_netbldgvalue", "amt_totalvalue", "dte_dateofsale", "amt_price",
    "txt_deedbook", "txt_deedpage", "situsaddress1", "txt_propertyuse_desc",
    "num_totalac", "municipality_desc",
)

DEFAULT_PAGE_SIZE = 2000
DEFAULT_TIMEOUT = 120


class ArcGisError(RuntimeError):
    """The service returned an error payload or could not be reached."""


class FeatureLayerClient:
    """Minimal paging client for an ArcGIS REST feature layer."""

    def __init__(
        self,
        layer_url: str = MECKLENBURG_CAMA_LAYER,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 4,
        opener: Optional[Callable[[str, int], bytes]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.layer_url = layer_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        self._open = opener if opener is not None else _urlopen_bytes
        self._sleep = sleep if sleep is not None else time.sleep

    def _query_url(self, params: Dict[str, str]) -> str:
        return self.layer_url + "/query?" + urllib.parse.urlencode(params)

    def _get_json(self, url: str) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                payload = json.loads(self._open(url, self.timeout).decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as error:
                last_error = error
                self._sleep(2 ** attempt)
                continue
            if isinstance(payload, dict) and "error" in payload:
                # Service-level errors are not transient; do not retry them.
                raise ArcGisError(str(payload["error"]))
            return payload
        raise ArcGisError("request failed after " + str(self.max_retries) + " attempts: " + str(last_error))

    def count(self, where: str = "1=1") -> int:
        """Total records matching ``where``."""
        payload = self._get_json(
            self._query_url({"where": where, "returnCountOnly": "true", "f": "json"})
        )
        return int(payload.get("count", 0))

    def iter_features(
        self,
        *,
        where: str = "1=1",
        fields: Sequence[str] = CAMA_FIELDS,
        order_by: str = "objectid",
        limit: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield feature attribute dicts, paging until the service says done."""
        offset = 0
        yielded = 0
        while True:
            page_size = self.page_size
            if limit is not None:
                page_size = min(page_size, limit - yielded)
                if page_size <= 0:
                    return
            payload = self._get_json(
                self._query_url(
                    {
                        "where": where,
                        "outFields": ",".join(fields),
                        "returnGeometry": "false",
                        "orderByFields": order_by,
                        "resultOffset": str(offset),
                        "resultRecordCount": str(page_size),
                        "f": "json",
                    }
                )
            )
            features: List[Dict[str, Any]] = payload.get("features", [])
            if not features:
                return
            for feature in features:
                yield feature.get("attributes", {})
                yielded += 1
            offset += len(features)
            if not payload.get("exceededTransferLimit") and len(features) < page_size:
                return


def _urlopen_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "monitorclt/0.1 (+parcel-enrichment)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
        return bytes(response.read())
