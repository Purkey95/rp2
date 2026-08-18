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

from urllib.parse import urlencode

from .base import SourceAdapter


class ArcgisAdapter(SourceAdapter):
    platform = "arcgis"
    page_size = 1000

    def _build_url(self, entry, offset, zip_code):
        where = entry.get("where", "1=1")
        if zip_code and entry.get("zip_field"):
            where = f"{entry['zip_field']}='{zip_code}' AND ({where})"
        params = {
            "where": where,
            "outFields": "*",
            "f": "json",
            "outSR": "4326",
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
        offset = 0
        while True:
            url = self._build_url(entry, offset, zip_code)
            data = self._fetch_json(url)
            features = data.get("features", [])
            for feat in features:
                yield self._flatten(feat)
            # Stop when the server says there's no more, or a short page came back.
            if not data.get("exceededTransferLimit") or not features:
                break
            offset += len(features)
