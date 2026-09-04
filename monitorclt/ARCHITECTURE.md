# MonitorCLT 2.0 — Architecture and rationale

This is the from-scratch rebuild. Each section names what v1 did, what changed, and
where it lives. The last section lists improvements that were not in the original
plan but fell out of building it.

## 1. The primitive: a parcel-and-person graph, not a probate pipeline

v1 was one script joining estates to parcels. v2 has **mentions**: every name in
every record, with its role (`decedent`, `owner`, `grantor`, `debtor`, ...) and the
address it travelled with (`entities.index_mentions`). A `LinkKind`
(`resolve/resolver.py`) says which mentions are subjects and which are candidates;
the same loop links any pair. Adding a source is a `SourceSpec` in a county plugin
(`counties/mecklenburg.py`) — no new matcher. Six sources ship: estate cases,
parcels, deeds, foreclosures, tax delinquency, code enforcement.

## 2. Change is the product: bitemporal, append-only history

v1's `UNIQUE (county, pin)` overwrote on every pull. v2 never updates a record in
place (`history.py`). Each version has `observed_at`/`superseded_at` (system time)
and `effective_date` (the date the source asserts). `as_of()` answers "what did we
know on Tuesday"; `effective_as_of()` answers "what was true on Tuesday". Every
transition emits `record_created` / `record_changed` (with a field diff) /
`record_retired`, and source adapters layer domain events on top (`events.py`:
`owner_changed`, `estate_opened`, `foreclosure_filed`, `tax_delinquent`, ...).
Snapshot sources retire what disappeared; incremental sources only accumulate.

## 3. Ingestion is a framework with raw capture and contract tests

`sources/base.py` defines `Connector` + `SourceSpec`; `sources/transport.py`
injects HTTP (retry/backoff, honest user agent) or recorded fixtures. `ingest.py`
captures bytes first (`raw.py`, deduplicated by hash), then parses, versions,
indexes mentions, and emits events, recording per-run counts, the field set seen,
and watermarks. `sources/contract.py` parses the fixture and compares it to a
golden file so a county markup change fails CI before the pipeline silently returns
zero rows. `sources/html_tables.py` turns portal tables into rows with the stdlib
parser; the estate connector reads an HTML results page.

## 4. The review queue is the training set

Weights are log-odds in `resolve/rules.json`, seeded to reproduce v1's additive
scores. `resolve/model.py` is logistic regression with an L2 penalty *toward the
seed*, trained on `label` rows that reviewer decisions write (`review.decide`).
With a handful of labels the model stays where domain knowledge put it; with
hundreds the data wins. `evaluate.py` reports a reliability table and Brier score
so "calibrated" is measured, not claimed. On the fixture labels one reviewer
decision plus training halves the Brier score (0.125 → 0.062).

Hard gates (`resolve/gates.py`) are outside the model: same county, not an
organization, corroboration required, no middle-initial or suffix conflict. A
name-only match cannot auto-confirm however high its probability.

## 5. A reviewer UI that turns decisions into labels

`ui/review.html` served by `api.py`: one candidate per screen, both records side by
side with matching name tokens highlighted, evidence chips coloured by sign,
gate checks, deeds on the parcel, other signals on the parcel, per-feature log-odds
contributions, keyboard `c`/`r`/`s`, note field, reviewer identity remembered.
Each decision posts to `/api/matches/{id}/decision` and becomes a
`review_decision`, a `label`, an `access_log` row and a `match_confirmed` /
`match_rejected` event.

## 6. Joins that used to break silently

- **PIN lineage** (`normalize/pins.py`): `parcel_lineage` records splits, merges
  and renumbers; `resolve_current()` and `ancestry()` follow them.
- **Addresses** (`normalize/addresses.py`): parsed into number / directionals /
  street / suffix / unit / PO box / city / state / ZIP. `compare()` distinguishes
  exact (same unit) from street-level (unit differs) from none; C/O and ATTN
  routing is stripped. Swap in libpostal/usaddress behind `parse_address` without
  touching callers.
- **Geometry** (`normalize/geo.py`): haversine, bounding box, point-in-polygon on
  lat/lon, enough for boundary watchlists on SQLite; PostGIS takes over in
  deployment (`sql/postgres/001_core.sql`).

## 7. Compliance you can execute

`policy.py` is deny-by-default. `check_export` requires: confirmed status,
corroborating evidence or a reviewer's signature, no failed gate, no suppression
hit on the contact's name, the decedent's name, the mailing address, the parcel or
the county, the source record inside retention, and an authorized contact
(personal representative or estate attorney — the decedent is never the contact).
Exports carry exactly `EXPORT_FIELDS`. Every check, allowed or denied, is an
`export_log` row; every reviewer action, suppression and purge is an `access_log`
row. `purge_expired` applies `retention_policy` per source. `why_do_you_have_this`
returns the full trail for one lead id down to the raw capture's URL and hash.

Webhooks and digests use the same gate (`watch.payload_for`), so there is one exit.

## 8. Subscriptions and an API, not a CSV

`watch.py`: watchlists filter the event stream by county, ZIP, event kind, land
use, value floor, polygon or radius; notifications are deduplicated per
(watchlist, event); webhooks are HMAC-SHA256 signed; digests are text. `ids.py`
gives every lead a stable id. `api.py` exposes parcels, persons and matches as
permalinks with the evidence trail, plus the queue, decisions, events, rank,
watchlists, status, export and provenance.

## 9. County as a plugin

`counties/__init__.py` registers factories; `counties/mecklenburg.py` is ~150
lines of declarations. The package is a src-layout project with its own
`pyproject.toml`, tests and fixtures, so `git subtree split -P monitorclt` lifts it
into its own repository unchanged.

## 10. Observe the data

`quality.py` snapshots per (connector, county): freshness against the last
successful run, current row count, delta vs. the connector's own trailing history
(3σ anomaly), zero-row detection, parse-failure rate, schema drift (field set vs.
last run), the resolver's blocked-out rate and rolling auto-confirm precision from
labels. `monitorclt status --strict` exits non-zero on any anomaly, so it can gate
a scheduler.

## Improvements found along the way

Things not in the original ten that turned out to matter:

- **Related party on the candidate record.** When the personal representative's
  name is a co-owner on the parcel (a surviving spouse), that is strong evidence
  the decedent on the same string is the right one. New feature
  `related_party_on_candidate`; it fires on the fixture's first estate.
- **Deed *to* the decedent.** v1 only used deeds *from* the decedent. A recorded
  acquisition (`deed_grantee_link`) corroborates ownership and, with the grantor
  link, gives the whole chain.
- **Street-level address agreement.** "4210 Elm St Apt 5" vs "4210 Elm St" is
  weaker than an exact match but far from nothing; `street_address_match` is its
  own feature with its own weight.
- **Suffix conflict.** JR vs SR on the same name is two people; a hard gate.
- **Per-record county.** A Union parcel can appear in a Mecklenburg feed; identity
  follows the record's own county, and retirement only touches counties the feed
  covered. v1 would have confirmed the out-of-county estate parcel; v2 sends it to
  the queue with `county_mismatch` visible.
- **The parcel signal stack** (`signals.py`). Multi-source is the point: an open
  estate plus a delinquent tax bill plus an out-of-state mailing address is a
  different situation from any one alone. Each signal is a documented public-record
  fact with a fixed weight; the score is the sum; the explanation is the list.
  Negative signals (post-death conveyance, recent owner change) pull a parcel down.
- **Reviewer time.** `review_decision.seconds_spent` is recorded, so the ten-second
  target is measurable, and slow decisions point at what the UI is not showing.
- **Input-gap detection generalized.** Parcels whose owner reads ESTATE OF / HEIRS
  with no linked case are a signal (`estate_marker_on_owner`) rather than a footnote,
  which is also the hint that a county or date range has not been pulled.
- **Raw-capture provenance on every lead.** `why_do_you_have_this` ends at the
  URL, timestamp and hash of the bytes the record came from.
- **Failed runs keep their watermark.** A transport failure never advances the
  watermark or retires rows, so an outage cannot look like a mass disappearance.

## What is deliberately not here yet

- **Live connectors.** Endpoints default to fixtures; real county portals need
  session handling, paging and rate limits that should be written against captured
  bytes from those sites, then frozen as fixtures here.
- **A Postgres `Store`.** The DDL ships; the query layer is SQLite-flavoured in a
  few places (`INSERT OR IGNORE`, `ON CONFLICT`). A second store class is the plan.
- **Authentication beyond a shared token.** The API takes `X-MonitorCLT-Token`;
  a real deployment fronts it with an identity-aware proxy and passes the reviewer
  identity as a header.
- **Orchestration.** `ingest → resolve → watchlist run → deliver → status --strict`
  is a cron line today; a Dagster/Prefect asset graph would add lineage and
  backfills.
- **Learned blocking.** Blocking is still FIRST|LAST. Nickname tables and
  phonetic keys would raise recall on the blocked-out cases the evaluator surfaces.
