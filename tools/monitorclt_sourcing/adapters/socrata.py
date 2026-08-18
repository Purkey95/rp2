"""Generic Socrata (SODA) adapter.

One connector for EVERY Socrata open-data portal — the other format most cities
use for code violations, permits, tax sales, and licenses. Coverage grows by
appending a registry entry. Shares base.normalize_row with the ArcGIS adapter.

Socrata SODA contract:
    https://{domain}/resource/{dataset_id}.json?$limit=&$offset=&$where=
Rows are already flat JSON objects, so no geometry flattening is needed; a
registry column_map names the lat/lng columns if the dataset carries them.
"""

from urllib.parse import urlencode

from .base import SourceAdapter


class SocrataAdapter(SourceAdapter):
    platform = "socrata"
    page_size = 1000

    def _build_url(self, entry, offset, zip_code):
        where = entry.get("where")
        if zip_code and entry.get("zip_field"):
            zclause = f"{entry['zip_field']}='{zip_code}'"
            where = f"{zclause} AND ({where})" if where else zclause
        params = {"$limit": self.page_size, "$offset": offset}
        if where:
            params["$where"] = where
        base = f"https://{entry['domain']}/resource/{entry['dataset_id']}.json"
        return f"{base}?{urlencode(params)}"

    def fetch_rows(self, entry, zip_code=None):
        offset = 0
        while True:
            url = self._build_url(entry, offset, zip_code)
            rows = self._fetch_json(url)
            if not rows:
                break
            for row in rows:
                yield row
            if len(rows) < self.page_size:
                break
            offset += len(rows)
