"""Generic JSON-API adapter — the vehicle for portal backends.

Tyler Citizen Self Service (CSS), DevNet Wedge, CentralSquare Evolve, and Accela
Citizen Access are all JavaScript single-page apps that fetch JSON from a backend
REST endpoint. Rather than scrape rendered HTML, this adapter calls that JSON
endpoint directly — the same registry-driven, config-only pattern as the ArcGIS
and Socrata adapters, so a portal becomes one more registry entry.

You point it at a portal by discovering its backend call (open the portal, watch
the browser Network tab, find the JSON request) and describing it in the registry:

  {
    "registry_id": "someco-css-permits",
    "platform": "json_api",
    "source_name": "Somewhere County Permits (Tyler CSS)",
    "request": {
      "url": "https://energov.someco.gov/EnerGovProd/selfservice/api/energov/search/search",
      "method": "POST",
      "headers": {"Content-Type": "application/json"},
      "body": {"Keyword": "", "ModuleId": 1, "SearchType": "Permit"},
      "page_param": "PageNumber",        // key to bump for each page (query or body)
      "page_in": "body",                  // "body" | "query"
      "page_start": 1,
      "page_size_param": "PageSize",
      "page_size": 100
    },
    "records_path": "Result.EntityResults",   // dotted path to the array of rows
    "record_url_template": "https://energov.someco.gov/.../permit/{CaseNumber}",
    "column_map": { ... }, "status_to_bucket": { ... }, "type_map": { ... }
  }

Everything after fetch reuses base.normalize_row, so the five governance rules
(anti-fabrication, no-guess classification/geography/bucket, quarantine-don't-stop)
apply to portal data exactly as they do to ArcGIS/Socrata.

NOTE ON ACCESS: some portals sit behind bot-protection or need a session cookie /
CSRF token. When a plain request is refused, the fallback is the Playwright-driven
`browser_api` path (drive the real portal, capture the same JSON) — documented in
PORTALS.md. Prefer rung 1-3 (existing GIS layer / official API / bulk records
request) before either of these.
"""

import json as _json
import urllib.request

from .base import SourceAdapter


def _dig(obj, dotted):
    """Read a dotted path (e.g. 'Result.EntityResults') from nested JSON."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _set_dotted(obj, dotted, value):
    """Set a dotted path inside a nested dict, creating intermediate dicts."""
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def default_fetch_json(request):
    """Live fetch honoring the registry's request spec (GET or POST JSON)."""
    method = request.get("method", "GET").upper()
    url = request["url"]
    headers = dict(request.get("headers", {}))
    data = None
    if method == "POST":
        headers.setdefault("Content-Type", "application/json")
        data = _json.dumps(request.get("body", {})).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return _json.loads(resp.read().decode("utf-8"))


class JsonApiAdapter(SourceAdapter):
    platform = "json_api"

    def __init__(self, fetch_json=None):
        # fetch_json here takes the whole request spec (not a URL), so the same
        # injection point serves fixtures (tests) and live portal calls.
        super().__init__(fetch_json or default_fetch_json)

    def _paginates(self, request):
        return bool(request.get("page_paths") or request.get("page_param"))

    def _paged_requests(self, request, zip_code):
        """Yield request specs, one per page, bumping the page number each time.

        Two paging shapes:
          - page_paths: a list of dotted locations in the body where the page
            number must be set (Tyler CSS wants it BOTH top-level and inside
            PermitCriteria); page_size_paths likewise. This is the general form.
          - page_param/page_in: the simple single-location form.
        """
        page = request.get("page_start", 1)
        size = request.get("page_size")
        while True:
            spec = _json.loads(_json.dumps(request))  # deep copy
            if request.get("page_paths"):
                body = spec.setdefault("body", {})
                for path in request["page_paths"]:
                    _set_dotted(body, path, page)
                if size:
                    for path in request.get("page_size_paths", []):
                        _set_dotted(body, path, size)
            elif request.get("page_param"):
                target = spec["body"] if request.get("page_in") == "body" else \
                    spec.setdefault("query", {})
                target[request["page_param"]] = page
                if size and request.get("page_size_param"):
                    target[request["page_size_param"]] = size
            yield spec, page
            if not self._paginates(request):
                break
            page += 1

    def fetch_rows(self, entry, zip_code=None):
        request = entry["request"]
        records_path = entry.get("records_path", "")
        max_pages = entry.get("max_pages", 50)   # safety bound
        seen_pages = 0
        for spec, page in self._paged_requests(request, zip_code):
            seen_pages += 1
            data = self._fetch_json(spec)
            rows = _dig(data, records_path) if records_path else data
            rows = rows or []
            for row in rows:
                yield row
            # Stop on a short/empty page or the page bound.
            size = request.get("page_size")
            if not self._paginates(request):
                break
            if not rows or (size and len(rows) < size) or seen_pages >= max_pages:
                break
