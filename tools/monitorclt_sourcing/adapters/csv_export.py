"""Generic CSV-export adapter.

Some portals have no JSON API but do expose a CSV download — e.g. DevNet Wedge
(`/search/csv`), and many county sites publish bulk CSVs. This adapter fetches
CSV text, parses it, and hands rows to base.normalize_row, so the same five
governance rules apply.

Two portal-isms it handles from the registry `request` spec:
  - enum_param / enum_terms: some search-CSV endpoints require a non-empty query
    and return only matches. To enumerate everything, iterate the query over a
    list of broad terms (e.g. the alphabet) and de-duplicate by a key column.
  - page_param / page_size: standard paging.

The injected fetch callable takes a URL and returns the raw CSV body (string),
so tests run offline against fixture CSV text.
"""

import csv
import io
import urllib.request
from urllib.parse import urlencode

from .base import SourceAdapter


def default_fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MonitorCLT-sourcing"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


class CsvExportAdapter(SourceAdapter):
    platform = "csv_export"

    def __init__(self, fetch_json=None):
        # Here the injected callable is a text fetcher (url -> csv string).
        super().__init__(fetch_json or default_fetch_text)

    def _fetch_text(self, url):
        return self._fetch_json(url)

    def fetch_rows(self, entry, zip_code=None):
        req = entry["request"]
        terms = req.get("enum_terms") or [None]
        dedup_field = req.get("dedup_field")
        seen = set()
        page_size = req.get("page_size")
        for term in terms:
            page = req.get("page_start", 1)
            while True:
                params = dict(req.get("query", {}))
                if term is not None and req.get("enum_param"):
                    params[req["enum_param"]] = term
                if req.get("page_param"):
                    params[req["page_param"]] = page
                    if req.get("page_size_param") and page_size:
                        params[req["page_size_param"]] = page_size
                url = f"{req['url']}?{urlencode(params)}"
                text = self._fetch_text(url)
                rows = list(csv.DictReader(io.StringIO(text)))
                for row in rows:
                    if dedup_field:
                        key = row.get(dedup_field)
                        if key in seen:
                            continue
                        seen.add(key)
                    yield row
                if not req.get("page_param") or not rows or (page_size and len(rows) < page_size):
                    break
                page += 1
