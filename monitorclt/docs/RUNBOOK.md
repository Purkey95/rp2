# MonitorCLT Runbook

Operating procedures for the three things that cannot be done from inside the repo:
bringing a live county source online, building a real labeled set, and splitting
the package into its own repository. Plus the daily run and what to do when it pages.

## 1. Daily run

```
monitorclt --db "$MONITORCLT_DB" run-daily --county ALL --profile live \
    --alert-webhook "$MONITORCLT_ALERT_WEBHOOK" --base-url http://127.0.0.1:8765/
```

`--county ALL` runs every county that has a `live` profile registered (today:
Mecklenburg). Yes, run every county you have live sources for, every day: the
sources are incremental after the first snapshot, the run is a few hundred requests
per county at a one-second pace, and the value of the system is the change stream,
which only exists if you pull daily. Add a county by registering its plugin under
the `live` profile; it joins the schedule with no other change. Exit code 1 means a
connector failed or `status` found an anomaly; the summary is printed and POSTed to
the alert webhook. Steps: ingest, resolve, cluster, watchlists, deliver, status.

### On the Mac mini (launchd)

```
mkdir -p "$HOME/Library/Application Support/MonitorCLT" "$HOME/Library/Logs/MonitorCLT"
cat > ~/.monitorclt.env <<'ENV'
export MONITORCLT_API_TOKEN="$(openssl rand -hex 24)"
export MONITORCLT_ALERT_WEBHOOK=""          # optional: Slack/Teams incoming webhook
export MCLT_WEBHOOK_SECRET_DEFAULT="$(openssl rand -hex 24)"   # referenced as env:MCLT_WEBHOOK_SECRET_DEFAULT by watchlists
ENV
chmod 600 ~/.monitorclt.env
cd /path/to/repo/monitorclt
for f in daily api; do
  sed "s|__HOME__|$HOME|g; s|__REPO__|$(cd .. && pwd)|g" deploy/launchd/com.monitorclt.$f.plist > ~/Library/LaunchAgents/com.monitorclt.$f.plist
  launchctl load -w ~/Library/LaunchAgents/com.monitorclt.$f.plist
done
launchctl start com.monitorclt.daily        # first run now; the snapshot takes a while
tail -f ~/Library/Logs/MonitorCLT/daily.log
```

- The daily agent runs at 06:10 local and, being a LaunchAgent with a calendar
  interval, runs at next wake if the machine was asleep. Keep the mini on mains
  power and disable sleep in Energy Saver anyway.
- The API agent binds to 127.0.0.1 only and reads the token from
  `~/.monitorclt.env`. To reach it from another machine use an SSH tunnel
  (`ssh -L 8765:127.0.0.1:8765 mini`), not a bind to 0.0.0.0.
- The database lives in `~/Library/Application Support/MonitorCLT/`. Back it up
  with Time Machine or a nightly `sqlite3 ... ".backup"`; it is the system of record
  for labels, decisions, outcomes and the audit log.
- Secrets never go in the plist (every process of the user can read it); they are
  sourced from the 600-mode env file by the command line.

When it pages:

| Anomaly | Meaning | Do |
|---|---|---|
| `last_run_failed` / `never_succeeded` | transport or parse blew up | read `ingest_run.error`; if the portal changed, capture the new page (below) and fix the parser against it |
| `zero_rows` | parser returned nothing on a source that had rows | almost always markup drift; the raw body is in `raw_capture` |
| `schema_drift:-a+b` | field set changed | check the golden diff; a renamed field silently breaks mentions and events |
| `delta_anomaly` | rows changed far more than usual | a re-plat, a reappraisal year, or a bad pull; inspect `record_changed` events before trusting downstream |
| `stale:Nh` | no successful run in N hours | scheduler or credentials |

Secrets: watchlist webhook secrets are stored as `env:NAME` references and read at
delivery; the API takes `--trust-proxy-header x-forwarded-user` behind an
identity-aware proxy, or a shared `--token` for a single operator.

## 2. Live sources already wired (Mecklenburg)

`--profile live` uses these City of Charlotte ArcGIS layers (`sources/arcgis.py`,
`counties/mecklenburg_live.py`):

| Source | Layer | Rows | Mode |
|---|---|---|---|
| `parcel` | `Accela/Accela/MapServer/16` (Parcel XAPO, assessor roll) | ~450k | snapshot, 4000/page |
| `code_enforcement` | `HNS/CodeEnforcementCasesAll/MapServer/0` | ~430k | incremental on `DateCreated` |
| `lien` | `ODP/FMSLienData/MapServer/0` (table) | ~25k | snapshot |
| `vacant_land` | `PLN/VacantLand/MapServer/0` | ~27k | snapshot |
| `address_point` | `CountyData/MasterAddress/MapServer/0` | ~677k | snapshot, 5000/page; fills `geocode_cache` and parcel coordinates |
| `deed` | `meckrod.manatron.com` Register of Deeds index (Aumentum) | ~85 deeds/day | incremental on date filed, 20 rows/page |

Fixtures: the committed pages under `fixtures/mecklenburg/live/` are real captures
with **every person-name token replaced by a stable pseudonym** (surnames become
three- or four-syllable words that exist nowhere; markers like ESTATE OF, HEIRS,
TRUST, initials, suffixes and organization words are kept, so the parsers see the
real shapes). Raw captures live in `fixtures-private/` (git-ignored). To refresh:

```
python3 scripts/capture_mecklenburg_live.py fixtures-private/mecklenburg/live
MCLT_PSEUDONYM_SALT="$(openssl rand -base64 32)" \
  python3 scripts/pseudonymize_fixtures.py fixtures-private/mecklenburg/live fixtures/mecklenburg/live
python3 -m monitorclt --db tmp.db contract --county MECKLENBURG --profile live --fixtures fixtures/mecklenburg/live --golden --write-golden
```

The salt is never committed; a fresh one per refresh means pseudonyms are not stable
across refreshes, which is intended. Addresses, parcel ids, values and dates are left
intact: they describe property, not people.

Operational notes: the first parcel snapshot is ~115 requests; schedule it weekly and
use `dateofsale >= TIMESTAMP` for a daily incremental of sales if the full snapshot
is too heavy (`ParcelXapoConnector.base_where`). The server returned no
`copyrightText`; confirm the city's open data terms before commercial use.

### The deeds index (Aumentum ROD Web Access)

`sources/aumentum.py` drives `meckrod.manatron.com` exactly as a browser does, with
field values recorded from a real session: accept the disclaimer (`__EVENTTARGET =
ctl00$cph1$lnkAccept`), load `/RealEstate/SearchEntry.aspx`, post the search with the
Infragistics date state (`|0|01YYYY-M-D-0-0-0-0||...`), document-type checkboxes
whose index the connector reads off the form (they differ per site), then GET
`/RealEstate/SearchResults.aspx?pg=N`. The record count and page span come from the
`_TotalRows`, `_StartRow`, `_EndRow` spans. Default document types: DEED, EXTR EST
(executor deed), QCD, TR/D, C/D, COM/D, SHF/D, FORECLOS, NOTC FOR, SUB TR, LIS/P,
EST TAX. The search form also offers a **grantor role** filter (EXR, ADMR, EST,
DECEASED, P/R) — a direct estate-conveyance query worth adding as a second pull.

Manners: `HttpTransport(cookies=True, min_interval_s=1.0)`, one narrow date window
per day, never a bulk historical crawl; the site's disclaimer places no restriction
on automated use. Rows carry only the first grantor and grantee (a `(+)` marks more);
the full party list is on the document detail page and is not fetched. The
`fixtures/mecklenburg/live/deed/*.html` pages are a two-day DEED search captured
2026-09-04.

Other counties: the NCARD directory (ncard.us/find-your-register-of-deeds) lists all
100 registers. Counties on the same Aumentum application need only a `base_url`;
Cott (`cotthosting.com`, ~10 counties) and the others need their own adapter.

### Estate cases: what is and is not allowed

- **eCourts Portal** (`portal-nc.tylertech.cloud`) is view-only and its terms
  prohibit automated access and scraping. Do not point a connector at it. A person
  may search it (case type "Decedents' Estate – Full Administration" / "Small
  Estate", by party name or date) and save the page; the estate connector reads a
  saved page.
- **Remote Public Access Program** (NCAOC): licensed statewide access. Online access
  is $495 setup plus $0.39 per transaction; extract access needs a $5,000 bond and
  currently offers criminal extracts and the civil *tax liens* extracts only. There
  is no estates extract. Ask the RPA office whether a custom civil/estates extract
  can be licensed; the "Required Information from Prospective Licensees" form starts
  the process.
- **Clerk of Superior Court, Mecklenburg estates division**
  (Mecklenburg.Estates@nccourts.org) fills requests for estate files, including
  pre-October-2023 cases that are not in the Portal.
- **Register of Deeds as a proxy**: executor/administrator deeds (EXTR EST) and the
  grantor-role filter (EXR, ADMR, EST, DECEASED) identify estates conveying property
  after the fact, without the court record.

### Regrid

`app.regrid.com` is a commercial product; the web app is not a data source and its
terms forbid scraping it. Its REST API (self-serve token, paid tiers; the trial token
is limited to seven counties) returns a standardized parcel schema with owner,
mailing address, values, sales, and boundaries, with `/parcels/query` filters on
owner name, FIPS, ZIP and land use, plus bulk delivery and an MCP server. For
Mecklenburg it adds nothing the assessor roll does not already give for free; it is
the fallback for counties without an open feature service, and a `RegridConnector`
against `/parcels/query` is a small addition once a token exists.

## 3. Bringing a live source online (estates, other counties)

Do this once per source, against captured bytes, never against the live site in a loop.

1. **Check terms.** Read the portal's terms of use and robots file. Prefer official
   bulk downloads (assessor parcel extracts, Register of Deeds index exports) over
   page scraping. Keep the honest user agent in `HttpTransport`. Rate-limit to what
   the terms allow; the transport already backs off on 429.
2. **Capture.** Fetch the pages or files the connector will consume and save them
   verbatim under `fixtures/<county>/`:

   ```
   curl -sS -A "MonitorCLT/2.0 (+public records monitor)" "$URL" -o fixtures/mecklenburg/estate_cases.html
   ```

   Redact nothing in the fixture: the parser must see what production sees. If the
   page contains live personal data, keep the fixture out of the public repo
   (`fixtures-private/`, git-ignored) and commit a synthetic one with the same markup.
3. **Point the connector at it.** Set `endpoint` in the county plugin to the live URL
   and, for tests, pass `--fixtures` so `FixtureTransport` serves the captured file
   under the same name.
4. **Write or adjust the parser** until `monitorclt contract --county X --fixtures ...
   --golden --write-golden` produces records with no problems; review the golden JSON
   by hand once, then commit it. From then on CI fails when the parser's output moves.
5. **Declare the spec.** Natural key, valid-time date field, which fields name people
   and in what role, address fields, PIN fields, event rules, retention. This is the
   only thing the rest of the system reads.
6. **Run it for a week on a scratch database** before pointing watchlists at it. Watch
   `status` for delta anomalies and the resolver's blocked-out rate.

## 4. Building the labeled set

The fixture labels are synthetic. Before trusting any threshold:

1. Pull one quarter of estate cases and the parcel roll for the county.
2. Run `resolve`, then export candidates with `review-queue --json` and `groups --json`.
3. Label at least 300 estate/parcel pairs, stratified so the hard cases are
   over-represented: common surnames (top 50 in the county), junior/senior pairs,
   remarriages (PR surname differs), trust-held parcels, out-of-county parcels, and
   at least 50 pairs the resolver *blocked out* (find them by searching the parcel roll
   by hand for a sample of estates with no candidates).
4. Two labelers, independently, on a 20 percent overlap. Run `agreement` and resolve
   disagreements before training.
5. `import-labels`, `train`, `evaluate --target-precision 0.95`. Ship the lowest
   auto-confirm threshold that clears the target; keep the reliability table in the
   release notes.
6. Turn on the audit sample (10 percent of auto-confirms into the queue) and re-run
   `evaluate` monthly. Precision is a time series, not a number.

## 5. Splitting into its own repository

```
git subtree split -P monitorclt -b monitorclt-standalone
git push git@github.com:<org>/monitorclt.git monitorclt-standalone:main
```

Then in the new repo: move `.github/workflows/monitorclt.yml` to `.github/workflows/ci.yml`
and drop its `paths:` filters and the `monitorclt/` prefixes. Nothing in the package
references the parent repository.

## 6. Review operations

- Reviewers work in the **Estates** tab (one estate, all candidates) by default. The
  **Pairs** tab is ordered by review value (uncertainty × parcel value). The **Audit
  sample** tab is a random slice of auto-confirms; decisions there are the unbiased
  precision measurement.
- Record outreach outcomes with `monitorclt outcome <outcome> --lead-id ... --by ...`
  or `POST /api/outcomes`. A `declined` suppresses the contact, address and parcel
  immediately. `not_in_estate` and `already_sold` become negative parcel signals.
- `outcomes-report` says which signals and evidence produce conversations. Re-weight
  `signals.SIGNALS` from it, not from intuition.
- Cadence: one export per estate per 30 days (`policy.CADENCE`). An export is the
  proxy for a contact attempt.
