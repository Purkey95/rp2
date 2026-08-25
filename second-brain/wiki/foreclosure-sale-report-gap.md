---
type: answer
created: 2026-08-25
updated: 2026-08-25
tags: [foreclosure, monitorclt, data-gap, mecklenburg]
---

# Are we capturing the Mecklenburg sale-report data?

**Partially. MonitorCLT captures both ends of the foreclosure timeline and misses
the middle — which is the only part you can still act on.**

Source: Mecklenburg DAILY REPORT OF SALE FILE, Carolina Data Integration
(704-477-7232), forwarded 2026-08-25 by Travis Mercer. Parser and analysis in
`second-brain/tools/foreclosure-sale-report/`.

## The timeline, and where the blind spot is

```
NOTICE OF SALE  ──►  AUCTION  ──►  10-DAY UPSET WINDOW  ──►  TRUSTEE DEED RECORDED
     ✓ captured          ✗              ✗ BLIND SPOT              ✓ captured
   mecktimes,                                                   excise stamps,
   hutchens,                                                    weeks later
   brock_scott
```

MonitorCLT's own outputs establish both ends. Foreclosure alerts carry pre-sale
notices with SP case numbers and sale dates. The daily brief reports "51
completed · 70.6% won by third parties · median clearing price $219,000 (excise
stamps)" — derived from recorded trustee deeds, which are filed only after the
upset period closes and the sale is confirmed, typically weeks after the hammer.

**Caveat on method:** the MonitorCLT codebase was not reachable from this
session, so this is inferred from the system's own alert output, not read from
its source. Nothing in any alert seen to date mentions an upset deadline, a high
bidder, or a deposit amount. Worth confirming directly against the pipeline list.

## What this report contains that the two ends do not

Eight records parsed from a single day:

| Field | Why it matters | In MonitorCLT? |
|---|---|---|
| **UPSET DEADLINE** + **AMT FOR DEPOSIT** | The 10-day window where the property is still acquirable by anyone with 5% + deposit | No |
| **HIGH BIDDER** + phone | Who is actually buying, named, with a number | Only via deed grantee, weeks later, no phone |
| **BID AMT** vs **TAX VALUE** | Clearing ratios computable same-day | Monthly-ish, from excise stamps |
| **FILE TYPE** (ROS / UPSET) | Distinguishes first report of sale from an upset resale | No |
| **PICTURE LINK** | Contains the Mecklenburg parcel id | No — and this solves address→property_id |

## What one day's data says

- **8 sales · $1.80M bid volume · median bid at 79.4% of tax value**
- **5 third-party wins, 3 lender takebacks (62.5% third party)** — the three
  lender takebacks (PennyMac ×2, LoanDepot) are an REO supply forecast: those
  become listings in 60–90 days. MonitorCLT currently sees REO only once it hits
  the books (442 parcels, ~$291M).
- **Four Corners of Charlotte LLC won two of eight in one day**, phone
  704-713-2602. That is a named, reachable, high-volume competitor — and a
  Pillar 6 referral target.
- One outlier worth a look: **26CV005576-590**, an uptown condo at 210 N Church
  St Unit 1601 taken for a **$5,000** bid. A CV case number rather than SP
  suggests an HOA or lien foreclosure rather than a mortgage foreclosure.
- **Six of eight carry a Mecklenburg parcel id** in the picture link.

## Three consequences

1. **The upset window is a live acquisition channel you cannot currently see.**
   Nine days remained on four of these when the report arrived. By the time the
   trustee deed records, the window is closed and the property is gone.
2. **It closes the [[seller-engine-blueprint-review]] F3 gap partially and the
   address-resolution problem substantially.** The parcel id in the picture link
   is an authoritative address↔parcel mapping arriving daily, free, for exactly
   the distressed properties that matter most.
3. **It sharpens the Foreclosure Auction Report** in
   [[marketing-strategy-monitorclt]] from a monthly lagging statistic to a
   same-day clearing ratio with a named-buyer leaderboard.

## Before ingesting: two cautions

- **The email carries CSV and TAB attachments.** Ingest those, not the PDF. The
  PDF is two-column and the columns interleave; parsing it is a fallback.
- **This is a paid, copyrighted compilation** ("Copyright 2006 Central Carolina
  Computers — Unauthorized reproduction strictly prohibited"), and the
  subscription belongs to a third party who forwarded it. The underlying facts
  are public record from the Clerk of Court, so they can be sourced directly —
  but **republishing this vendor's compilation in a public marketing report is a
  licensing question, not a technical one.** Either license it explicitly for
  that use, or scrape the primary source for anything published.

Related: [[monitorclt]] · [[marketing-strategy-monitorclt]] · [[seller-engine-spec]]
