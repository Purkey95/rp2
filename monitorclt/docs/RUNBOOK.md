# MonitorCLT Runbook

Operating procedures for the three things that cannot be done from inside the repo:
bringing a live county source online, building a real labeled set, and splitting
the package into its own repository. Plus the daily run and what to do when it pages.

## 1. Daily run

```
monitorclt --db /var/monitorclt/prod.db run-daily --county MECKLENBURG \
    --alert-webhook "$ALERT_URL" --base-url https://monitorclt.internal/
```

Exit code 1 means a connector failed or `status` found an anomaly; the summary is
printed and POSTed to the alert webhook. Cron at 06:00 local, after the county
systems' overnight loads. Steps: ingest, resolve, cluster, watchlists, deliver, status.

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

Capture procedure used for the fixtures (repeat to refresh them): POST to each
layer's `/query` with `f=json&outFields=*`, save each page verbatim as
`fixtures/mecklenburg/live/<source>/<n>.json`, set `exceededTransferLimit` on all
but the last page. The fixtures are public records published by the county; keep
the sample small.

Operational notes: the first parcel snapshot is ~115 requests; schedule it weekly and
use `dateofsale >= TIMESTAMP` for a daily incremental of sales if the full snapshot
is too heavy (`ParcelXapoConnector.base_where`). The server returned no
`copyrightText`; confirm the city's open data terms before commercial use.

Still missing for the probate link: estate cases and the deed index. See the next section.

## 3. Bringing a live source online (estates, deeds)

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
