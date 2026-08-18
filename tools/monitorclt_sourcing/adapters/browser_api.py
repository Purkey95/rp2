"""Browser-automation adapter (Playwright) — last-resort transport.

For portals that only yield their data to a real browser: classic ASP.NET
WebForms search grids (Accela ACA), Angular/React SPAs with no callable backend,
and pages behind a JavaScript challenge (Cloudflare Turnstile). It drives real
Chromium, then hands rows to base.normalize_row like every other adapter.

Two extraction modes (registry `browser.mode`):
  - "json": capture the JSON the page's own XHR/fetch returns — set
    `capture_url_contains` to the backend path and `records_path` to the array.
    Best when the SPA has a backend but it needs the page's session/cookies.
  - "table": scrape a rendered HTML table — `table_selector` finds the <table>;
    the header row becomes the dict keys. Best for WebForms result grids.

IMPORTANT — where this runs:
  * It needs Playwright + Chromium and REAL outbound networking. It is designed
    to run on the MonitorCLT host, NOT inside the Claude sandbox (headless browser
    egress is blocked here — curl/urllib work, the browser does not). So this
    adapter ships tested at the mapping layer (injected rows) but is exercised
    live only on the host.
  * A Cloudflare *managed challenge* (cf-mitigated: challenge) may still block
    headless automation. For those, persist a `cf_clearance` cookie from a real
    session (browser.storage_state), or prefer a bulk-records request (Rung 3).

On the host:  pip install playwright  (Chromium is already at /opt/pw-browsers,
so PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1; pass browser.executable_path if needed).
"""

from .base import SourceAdapter


def _default_browser_fetch(entry):
    """Drive Chromium and return a list of raw row dicts. Host-only (needs Playwright)."""
    from playwright.sync_api import sync_playwright  # lazy: keep package importable without it

    b = entry["browser"]
    mode = b.get("mode", "table")
    records_path = entry.get("records_path", "")
    rows = []

    launch_kwargs = {"headless": b.get("headless", True)}
    if b.get("executable_path"):
        launch_kwargs["executable_path"] = b["executable_path"]
    if b.get("proxy"):
        launch_kwargs["proxy"] = {"server": b["proxy"]}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(storage_state=b.get("storage_state"))
        page = ctx.new_page()

        captured = []
        if mode == "json" and b.get("capture_url_contains"):
            def _on_response(resp):
                if b["capture_url_contains"] in resp.url:
                    try:
                        captured.append(resp.json())
                    except Exception:
                        pass
            page.on("response", _on_response)

        page.goto(b["url"], wait_until=b.get("wait_until", "networkidle"),
                  timeout=b.get("timeout_ms", 60000))
        if b.get("wait_selector"):
            page.wait_for_selector(b["wait_selector"], timeout=b.get("timeout_ms", 60000))
        elif b.get("wait_ms"):
            page.wait_for_timeout(b["wait_ms"])

        if mode == "json":
            for payload in captured:
                arr = payload
                for part in records_path.split("."):
                    if part and isinstance(arr, dict):
                        arr = arr.get(part)
                if isinstance(arr, list):
                    rows.extend(arr)
        else:  # table mode: header row -> keys
            sel = b.get("table_selector", "table")
            data = page.eval_on_selector(sel, """(tbl) => {
                const rows = [...tbl.querySelectorAll('tr')];
                if (!rows.length) return [];
                const heads = [...rows[0].querySelectorAll('th,td')].map(c => c.innerText.trim());
                return rows.slice(1).map(r => {
                    const cells = [...r.querySelectorAll('td')].map(c => c.innerText.trim());
                    const o = {}; heads.forEach((h,i) => o[h] = cells[i] ?? ''); return o;
                });
            }""")
            rows.extend(data or [])

        browser.close()
    return rows


class BrowserApiAdapter(SourceAdapter):
    platform = "browser_api"

    def __init__(self, fetch_json=None):
        # injected callable takes the entry, returns a list of row dicts (tests
        # inject rows directly; live path drives Playwright on the host).
        super().__init__(fetch_json or _default_browser_fetch)

    def fetch_rows(self, entry, zip_code=None):
        for row in (self._fetch_json(entry) or []):
            yield row
