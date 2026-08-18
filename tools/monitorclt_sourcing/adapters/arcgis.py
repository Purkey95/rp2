"""Generic ArcGIS FeatureServer adapter.

One connector for EVERY ArcGIS/Esri open-data layer — the format most county GIS
portals use to publish building permits, parcels, tax data, code cases, and
zoning. Coverage grows by appending an entry to the registry, never by editing
this file. This is the ArcGIS twin of socrata.py and shares base.normalize_row.

ArcGIS REST query contract:
    {service_url}/query?where=<filter>&outFields=*&f=json&outSR=4326
    paged via resultOffset / resultRecordCount, honoring exceededTransferLimit.
Point geometry {x:lng, y:lat} is flattened to __lng/__lat so the registry's
column_map reads coordinates uniformly across platforms.
"""

import json
from urllib.parse import urlencode

from .base import SourceAdapter


class ArcgisAdapter(SourceAdapter):
    platform = "arcgis"
    page_size = 1000

    def _where(self, entry, zip_code):
        where = entry.get("where", "1=1")
        if zip_code and entry.get("zip_field"):
            where = f"{entry['zip_field']}='{zip_code}' AND ({where})"
        return where

    def _build_url(self, entry, offset, zip_code):
        params = {
            "where": self._where(entry, zip_code),
            "outFields": "*",
            "f": "json",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": self.page_size,
        }
        return f"{entry['service_url']}/query?{urlencode(params)}"

    def _build_groupby_url(self, entry, offset, zip_code):
        """groupBy fetch mode — for servers that block record-level outFields
        queries (e.g. Charlotte-Mecklenburg's Accela permit FeatureServers return
        HTTP 400 on outFields but allow statistics). We group by the identifying
        fields so each returned feature's attributes ARE one logical record.
        """
        fields = entry["groupby_fields"]
        stat_field = entry.get("groupby_stat_field", fields[0])
        out_stats = [{"statisticType": "count", "onStatisticField": stat_field,
                      "outStatisticFieldName": "row_count"}]
        params = {
            "where": self._where(entry, zip_code),
            "groupByFieldsForStatistics": ",".join(fields),
            "outStatistics": json.dumps(out_stats),
            "orderByFields": fields[0],
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": self.page_size,
        }
        return f"{entry['service_url']}/query?{urlencode(params)}"

    @staticmethod
    def _flatten(feature):
        """Merge attributes + flattened point geometry into one row."""
        row = dict(feature.get("attributes", {}))
        geom = feature.get("geometry") or {}
        if "x" in geom and "y" in geom:
            row["__lng"], row["__lat"] = geom["x"], geom["y"]
        return row

    def fetch_rows(self, entry, zip_code=None):
        groupby = entry.get("fetch_mode") == "groupby"
        offset = 0
        while True:
            url = (self._build_groupby_url(entry, offset, zip_code) if groupby
                   else self._build_url(entry, offset, zip_code))
            data = self._fetch_json(url)
            features = data.get("features", [])
            for feat in features:
                # groupBy features have no geometry; attributes are the row.
                yield dict(feat.get("attributes", {})) if groupby else self._flatten(feat)
            # groupBy responses don't set exceededTransferLimit — page until a
            # short page (fewer than requested) comes back.
            if groupby:
                if len(features) < self.page_size:
                    break
            elif not data.get("exceededTransferLimit") or not features:
                break
            offset += len(features)
