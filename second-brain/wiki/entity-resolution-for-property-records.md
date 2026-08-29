---
type: concept
created: 2026-08-29
updated: 2026-08-29
tags: [monitorclt, entity-resolution, data-quality]
---

# Entity resolution for property records

The load-bearing component of [[monitorclt]]. Every person-keyed source
([[property-keyed-vs-person-keyed-sources]]) is worthless without it, and
[[life-event-property-intelligence-engine]] treats it as solved — asserting
`Owner Match Confidence: 96%` without saying where the number comes from.

## Why it is hard

- **Owner strings are not names.** Tax rolls carry
  `SMITH JOHN A & MARY B`, `SMITH JOHN A TRUSTEE`,
  `SMITH FAMILY REVOCABLE TRUST`, `JAS HOLDINGS LLC`,
  `SMITH JOHN A LIFE ESTATE`. Parsing these into people is its own project.
- **Obituaries frequently omit the address**, giving at most a town and an
  age. Name + town is a weak key in a county of ~1.1M people.
- **Name collisions are dense.** Common name + common town = many candidates.
- **LLC and trust ownership breaks the person link entirely** unless you can
  connect a registered agent or manager to a human.
- **Deceased owners linger in tax rolls** for years after death, which is
  simultaneously the opportunity and a source of stale records.

## Precision >> recall

A false negative costs one missed lead. A false positive means contacting
the wrong family about a death — reputational damage far out of proportion
to the value of the lead, and the specific failure mode that ends companies
in this space. Tune hard toward precision; leave recall on the table.

## Practical approach

- Emit **candidate sets with evidence**, not a single match with a
  confidence percentage. `3 candidates: parcel A (name+middle initial+town),
  parcel B (name+town), parcel C (name only)`.
- Require **at least two independent corroborating signals** before
  promoting a candidate to matched: age vs. deed date, surviving-spouse name
  vs. co-owner name, funeral home location vs. parcel location, an estate
  case naming the same decedent.
- Keep **human review in the loop** for the promotion step until the
  false-positive rate is measured. This is cheap at Mecklenburg volume.
- **Never auto-promote on name alone**, at any string-similarity threshold.

## Corroborating signals worth collecting

Middle initial/name; suffix (Jr/Sr/III); spouse name; age vs. length of
ownership; town; funeral home service area; estate case decedent name and
county; recorded deed history; co-owner names.

## Measurement

Track precision explicitly against a hand-labeled sample. A match rate
without a measured false-positive rate is not a result — see
[[property-signal-scoring-and-calibration]].
