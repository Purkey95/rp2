# Getting the portal-locked data (Tyler / Accela / Evolve / DevNet)

Several counties publish their highest-value distress signals — permits, tax
delinquency, code cases — only through vendor portals, not open APIs. Those
portals are not a wall; they're a ladder. Climb from the top: the higher rungs
are more stable, more legal, and less work.

## The ladder (best rung first)

### Rung 1 — Is it already in ArcGIS/Socrata?
Many portals mirror their data into the county GIS. Mecklenburg's and Cabarrus's
Accela permits are already ArcGIS layers (`AccelaAllPermits`, `Plan_Reviews`).
Tyler **owns Socrata**, so Tyler shops sometimes expose an open-data portal.
Always check the county GIS REST directory and any `data.<county>.gov` first —
if it's there, use the ArcGIS/Socrata adapter and stop.

### Rung 2 — Call the portal's JSON backend directly
Every one of these portals is a JavaScript single-page app that fetches JSON from
a backend endpoint. You don't scrape the rendered page — you call that endpoint.

**Discovery (5 minutes per portal):** open the portal, run a search, open the
browser DevTools → Network tab → filter XHR/Fetch, and read the request that
returns the results — its URL, method (usually POST), JSON body, and the path to
the results array in the response. Drop those into a `json_api` registry entry
(see `adapters/json_api.py` for the schema). Same governance as every other
adapter.

Per-vendor pointers (verify each against the live portal — versions vary):
- **Tyler EnerGov — Citizen Self Service (CSS):** SPA at `…/selfservice`. Backend
  search endpoint is typically `POST …/selfservice/api/energov/search/search`
  with a JSON body carrying `ModuleId` + `SearchType` (Permit / CodeCase /
  Plan). Results under `Result.EntityResults`. Public search usually needs no
  auth. (Iredell and Rowan are EnerGov shops.)
- **Accela — Citizen Access (ACA) + Construct API:** ACA is an older ASP.NET
  WebForms app (viewstate) — harder to call cleanly. Prefer Accela's official
  **Construct/Civic Platform REST API** (`apis.accela.com`, `/v4/records`) when
  the agency has it enabled and you register an app; ask the agency. If neither,
  ACA search can be driven with Rung 4. (St. Louis County MO and Union permits.)
- **DevNet (Grant Street Group) Wedge:** tax inquiry SPA (`*.devnetwedge.com`).
  The app calls JSON search endpoints under the same host; discover via DevTools.
  Grant Street also runs the county's tax-sale / auction site
  (`*.taxsale.*` / bid platforms) which may have their own API. (Union tax.)
- **CentralSquare Evolve — "Evolve Public":** community-development SPA
  (`*/evolvepublic/`). Backend JSON search endpoints under the same host. (Union
  code/permits.)

### Rung 3 — Just ask the county for a bulk extract (often the easiest)
For tax delinquency especially, skip the portal entirely:
- **The delinquent-tax advertisement is a public record by law.** NC counties
  must advertise unpaid tax liens annually (newspaper + a downloadable list/PDF
  on the tax office site). That list is name + parcel + amount — exactly the
  signal. Grab it once a year.
- **Public-records / bulk-data request.** NC public-records law entitles you to
  the underlying data. County tax and GIS offices routinely provide a CSV or DB
  extract for free or a small fee. One email to the county tax office or GIS
  department frequently beats weeks of scraping and is fully above board.

### Rung 4 — Browser automation (last resort)
When there's no reachable API and the site has bot-protection (e.g. St. Louis's
Collector pages 403 plain requests), drive the real portal with Playwright
(Chromium is preinstalled here) and capture the same JSON the SPA receives, or
read the rendered table. Brittle; rate-limit; honor robots/ToS. This maps to a
future `browser_api` adapter that shares `base.normalize_row` — same governance,
different transport.

### Rung 5 — Buy it
LienSuite, TaxLiens.com, and similar resell county tax-lien/foreclosure feeds. A
paid feed can be cheaper than maintaining a scraper for a low-value county.

## How this plugs in

Rungs 1-2 are already-built adapters (`arcgis`, `socrata`, `json_api`) — a portal
is one registry entry once you've discovered its JSON call. Rung 3 lands as a
`csv` adapter (the extract) or a one-off import. Rung 4 is the `browser_api`
adapter (not yet built). Every rung ends in the same `signals` table with full
provenance, so where the data came from is always recorded.

## Verified access notes (2026-08-18)

Findings from probing the actual portals — what rung each really lands on:

- **Union tax delinquency → SOLVED, no browser.** DevNet Wedge has no JSON API
  but exposes `/search/csv` (rung 2/3, wired via `csv_export`); the statutory
  delinquent-tax advertisement PDF (rung 3) parses cleanly and passed Akamai with
  browser headers alone (wired via `pdf`). Amounts come from the PDF, status +
  parcel from the CSV.
- **Iredell permits/code → SOLVED, rung 2.** Tyler EnerGov CSS exposes an
  anonymous JSON backend (`/energov_prod/selfservice/api/energov/search/search`,
  tenant headers required) — wired via `json_api`.
- **St. Louis permits → rung 2 (preferred) or rung 4.** Accela **Construct API v4**
  is live (`GET https://apis.accela.com/v4/records`, `x-accela-agency: SLC`,
  records at `result[]`) but needs an **App ID** (register at developer.accela.com)
  AND the county to have enabled public API access (unconfirmed). If enabled it's a
  `json_api` entry — no browser. Otherwise rung 4: ACA is ASP.NET WebForms
  (module `PublicWorks`), driven by `browser_api` table mode.
- **St. Louis 1st/2nd/3rd tax sale → rung 4, Cloudflare-gated.** The Collector
  pages return `HTTP 403 cf-mitigated: challenge` even with full browser headers —
  only a real browser that solves the JS challenge reaches the body. Use
  `browser_api` with a persisted `cf_clearance` cookie, or (more reliable) rung 3:
  request the list from the Collector of Revenue. (Post-third is already open via
  ArcGIS.)

- **Union permits/code (Evolve) → rung 3 or 4, both painful.** CentralSquare
  Evolve is ASP.NET WebForms with a broken web-farm ViewState (per-instance
  machineKeys, no session affinity) — ViewState-replay 500s with "viewstate MAC
  failed," and there is no JSON API and no ArcGIS permit layer. Permit search is
  anonymous but only via a single live browser session (browser_api, still fights
  the cluster). **Most reliable is a records request** to Union County Building
  Code Enforcement (ucinspections@unioncountync.gov, 704-283-3816). City of Monroe
  (separate) has a wireable CityView JSON endpoint, but city-only, permits-only,
  HTML-in-JSON — a different, partial dataset.

**Environment caveat.** The `browser_api` adapter needs Playwright + real outbound
networking; it runs on the **MonitorCLT host**, not in the Claude sandbox (headless
browser egress is blocked here — curl/urllib work, the browser does not). And a
Cloudflare managed challenge can resist headless automation even on a real host —
when it does, a bulk-records request beats fighting Turnstile.

## Priority for our counties (from the coverage research)

| County | Locked signal | Best rung to try |
|---|---|---|
| Union | tax delinquency | Rung 3 (delinquent-tax advertisement) → Rung 2 (DevNet Wedge JSON) |
| Union | permits/code | Rung 2 (Evolve Public JSON) |
| Gaston | tax foreclosure | Rung 3 (gastongov.com list) / Rung 5 (Kania listings) |
| Iredell / Rowan | permits/code (fuller) | Rung 2 (Tyler EnerGov CSS) — Rowan permits already open via ArcGIS |
| St. Louis MO | 1st/2nd/3rd sale | Rung 4 (Collector pages are bot-protected) |
| St. Louis MO | permits | Rung 2 (Accela Construct API) / Rung 4 (ACA) |
| Mecklenburg | pre-foreclosure list | Rung 3 (tax office) — realized foreclosures already open via ArcGIS |
