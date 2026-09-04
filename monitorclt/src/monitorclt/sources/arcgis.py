"""ArcGIS REST feature-layer connectors: paged queries, incremental date filters,
epoch-millisecond dates, and a fixture layout that replays the same pages offline.

Most county and city GIS portals expose their layers this way, so this is the base
for every source that is a feature layer or a table: parcels, code enforcement cases,
liens, vacant land, address points. A subclass supplies the layer URL, the natural
key, an optional incremental date field, and `to_record()` which maps the layer's
attributes onto the SourceSpec's fields.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from .base import Connector, Fetched
from .transport import Transport


def epoch_ms_to_date(value: Any) -> Optional[str]:
    """ArcGIS dates are epoch milliseconds (UTC). None and '' stay None."""
    if value in (None, ""):
        return None
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000.0, dt.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None


def strip(value: Any) -> Optional[str]:
    """The lien table pads every string to its column width."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def ring_centroid(geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Vertex-average centroid of the first ring; enough to place a parcel."""
    rings = (geometry or {}).get("rings") or []
    if not rings or not rings[0]:
        return None
    pts = rings[0]
    return {"lon": sum(p[0] for p in pts) / len(pts), "lat": sum(p[1] for p in pts) / len(pts)}


class ArcGisLayerConnector(Connector):
    """Pages through /query on a feature layer or table.

    Live: endpoint is the layer URL (".../MapServer/16"); pages are fetched with
    resultOffset until exceededTransferLimit is false. Fixture: endpoint is
    "fixture://<dir>" and pages are "<dir>/0.json", "<dir>/1.json", ... captured
    verbatim from the live service (see fixtures/mecklenburg/live).
    """

    layer_url: str = ""
    page_size: int = 2000
    order_by: str = "OBJECTID"
    incremental_field: Optional[str] = None  # date field for `since` filtering
    out_fields: str = "*"
    return_geometry: bool = False
    base_where: str = "1=1"
    min_interval_s: float = 0.5

    def __init__(self, county: str, endpoint: Optional[str] = None, sleep: Any = None) -> None:
        super().__init__(county)
        self.endpoint = endpoint or self.layer_url
        import time as _time

        self._sleep = sleep or _time.sleep
        self._last_request = 0.0

    def _pace(self) -> None:
        import time as _time

        if self.endpoint.startswith("fixture://"):
            return
        wait = self._last_request + self.min_interval_s - _time.time()
        if wait > 0:
            self._sleep(wait)
        self._last_request = _time.time()

    # ------------------------------------------------------------ fetch ---

    def where(self, watermark: Optional[str]) -> str:
        if watermark and self.incremental_field and self.spec.mode == "incremental":
            day = str(watermark)[:10]
            return "({0}) AND {1} >= TIMESTAMP '{2} 00:00:00'".format(self.base_where, self.incremental_field, day)
        return self.base_where

    def fetch(self, transport: Transport, watermark: Optional[str]) -> Iterable[Fetched]:
        offset = 0
        page = 0
        while True:
            if self.endpoint.startswith("fixture://"):
                url = "{0}/{1}.json".format(self.endpoint.rstrip("/"), page)
                body, ctype = transport.get(url)
            else:
                params = {
                    "where": self.where(watermark),
                    "outFields": self.out_fields,
                    "orderByFields": self.order_by,
                    "resultOffset": str(offset),
                    "resultRecordCount": str(self.page_size),
                    "returnGeometry": "true" if self.return_geometry else "false",
                    "f": "json",
                }
                if self.return_geometry:
                    params["outSR"] = "4326"
                url = "{0}/query?{1}".format(self.endpoint.rstrip("/"), urlencode(params))
                self._pace()
                body, ctype = transport.get(url)
            yield Fetched(body=body, url=url, content_type=ctype or "application/json")
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                return
            if payload.get("error"):
                raise RuntimeError("ArcGIS error from {0}: {1}".format(url, payload["error"]))
            n = len(payload.get("features", []))
            if not payload.get("exceededTransferLimit") or n == 0:
                return
            offset += n
            page += 1

    # ------------------------------------------------------------ parse ---

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = json.loads(body.decode("utf-8"))
        if payload.get("error"):
            raise ValueError("ArcGIS error: {0}".format(payload["error"]))
        out = []
        for feature in payload.get("features", []):
            rec = self.to_record(feature.get("attributes") or {}, feature.get("geometry"))
            if rec is not None:
                out.append(rec)
        return out

    def to_record(self, attrs: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:  # pragma: no cover - abstract
        raise NotImplementedError
