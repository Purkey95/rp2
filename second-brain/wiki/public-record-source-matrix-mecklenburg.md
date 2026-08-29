---
type: synthesis
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, mecklenburg, matrix]
---

# Public record source matrix — Mecklenburg (scoped)

[[life-event-property-intelligence-engine]] proposes a full NC-statewide,
all-event-type source matrix as the next step.
[[design-review-life-event-engine]] argues against: that is weeks of
research producing no running system, and county sites change faster than
the document can be maintained.

This is the scoped version — **Mecklenburg only**, ordered by
[[property-keyed-vs-person-keyed-sources]]. Expand it from something that
runs.

## Phase 1 — property-keyed (build these)

| Source | Access mode | Key | Verify |
|---|---|---|---|
| County tax roll / parcel + owner index | Download or GIS export | parcel id | Bulk download available? refresh cadence? |
| County GIS parcel layer | Shapefile / API | parcel id | Geometry + address normalization |
| Register of Deeds — deeds, deeds of trust | Search / possible bulk | parcel + instrument | Bulk terms; DoT presence is the mortgage proxy |
| Tax delinquency list | Published list | parcel id | Publication cadence |
| County tax foreclosure listings | Web page | parcel + sale date | Attorney assignment, auction dates |
| Foreclosure notices | [[nc-press-association-public-notices]] | SP case no. | Search/export terms |
| Code enforcement / violations | City portal or open data | address | Availability, backlog |
| Building permits | City/county open data | address | Useful as deferred-maintenance proxy |

## Phase 2 — person-keyed (after the index exists)

| Source | Access mode | Key | Verify |
|---|---|---|---|
| Estate notices to creditors | [[nc-press-association-public-notices]] | estate file no. | Address rarely present |
| Estate/probate cases | [[nc-ecourts]] | estate file no. | **Portal terms — blocking question** |
| Obituaries | Funeral homes / licensed feed | name + town | Aggregator ToS rules out Legacy/Echovita |
| Bankruptcy | [[pacer-cm-ecf]] | case no. | Which event types W.D.N.C. publishes |
| Judgments / liens | Courts + Register of Deeds | name | Name-only matching is weak |
| Divorce / partition | [[nc-ecourts]] | case no. | Partition names property; divorce often does not |

## Columns to fill per source, once checked

`url` / `access` (RSS, API, bulk download, search-only, scrape) /
`terms` (permitted? licensed? prohibited?) / `cost` /
`update cadence` / `lag behind the real event` /
`contains parcel or address?` / `contains person name?` /
`join key` / `fragility`

The two decisive columns are **terms**
([[distressed-property-outreach-compliance]]) and **contains parcel or
address** — together they determine whether a source is buildable and how
expensive it is once built.

## Deliberately deferred

Statewide NC collection; Tier 1 counties beyond Mecklenburg (Union,
Cabarrus, Gaston, Iredell, Lincoln, Rowan, Catawba, Burke); York and
Lancaster SC. The blocker for all of them is enrichment heterogeneity, not
collection — which is the buy-vs-build parcel-data decision open in
[[monitorclt]].
