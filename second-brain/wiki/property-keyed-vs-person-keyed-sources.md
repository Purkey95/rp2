---
type: concept
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, data-sources, architecture]
---

# Property-keyed vs person-keyed sources

The single most useful way to sort public-record sources for
[[monitorclt]]: **does the record already contain a parcel or address?**

## Property-keyed — the record names the property

Foreclosure notices, tax foreclosure listings, lis pendens, code
violations, building permits, recorded deeds and deeds of trust,
condemnation notices, rezoning cases.

Matching is nearly free: normalize the address, join to the parcel layer,
done. These sources are cheap to exploit and should be built first.

## Person-keyed — the record names only a human

Obituaries, bankruptcy petitions, divorce filings, judgments, liens against
individuals, probate/estate cases (which name a decedent, sometimes without
listing real property).

Every one of these requires [[entity-resolution-for-property-records]] before
it produces anything actionable. That is the expensive component.

## Why the ordering matters

[[life-event-property-intelligence-engine]] proposes starting with
obituaries — the hardest person-keyed source, matched against a parcel/owner
index that does not exist yet. Building property-keyed sources first
produces value immediately *and* constructs the index that person-keyed
matching later needs to match against.

## Bootstrapping sequence

1. Parcel + owner index (tax roll / GIS, or vendor)
2. Property-keyed event streams -> working product
3. Recorded-deed history -> owner name variants, trusts, LLC linkage
4. Person-keyed streams, matched against the index built in 1-3

## Partial cases

Some person-keyed records do name property — an estate inventory listing
real property, a foreclosure petition naming the borrower and the parcel, a
notice to creditors that references an address. Treat these as
property-keyed when the address is present and person-keyed when it is not;
the decision is per-record, not per-source.
