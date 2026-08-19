# Market & capital-market indicators — can we gather them, and which model?

Answering both indicator lists directly. Three models consume these, and keeping
them separate is the whole point:

- **Owner Distress Score** (parcel — *what/who*): the wired distress signals.
- **Market Timing** (metro/submarket — *when/where*): this module.
- **Deal Underwriting** (deal — *what kills returns*): inputs to a pro-forma.

Verdict key:
- **WIRED** — built and running now (this module).
- **FREE-ADD** — free API, same pattern, not yet wired (one config/adapter away).
- **OURS** — derivable from data MonitorCLT already pulls (permits, parcels, zoning).
- **PAID** — commercial data (Trepp, CoStar, MLS, CoreLogic).
- **BROWSER** — public record, but portal/court-gated (like the ROD liens).
- **NO** — not legally/practically obtainable at scale.

## Batch 1

### Leading (6–18 mo ahead)
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Building permits & starts | **WIRED** + **OURS** | FRED PERMIT/HOUST + Charlotte CHAR737BPPRIV; plus our own county permit counts | Market Timing |
| 10-yr Treasury + mortgage spread | **WIRED** | FRED DGS10, MORTGAGE30US → derived spread | Market Timing + Underwriting |
| Net absorption vs deliveries | **PAID** (MF) | CoStar/CBRE; SF only partially public | Market Timing |
| Months of supply / DOM trend | **FREE-ADD** | Redfin Data Center (free county/metro TSV) | Market Timing |
| Price-cut share, list-to-sale | **FREE-ADD** | Redfin Data Center | Market Timing |

### Coincident (where we are now)
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Job growth ÷ permit ratio | **WIRED** | derived `jobs_to_permits` (FRED CHAR737NA ÷ CHAR737BPPRIV) | Market Timing |
| Domestic net migration | **FREE-ADD** | Census pop. estimates + IRS SOI migration (annual, lagged) | Market Timing |
| Rent growth vs wage growth | **FREE-ADD** | Zillow ZORI (free) + BLS QCEW / FRED metro wages | Market Timing |
| Delinquency | **WIRED** | FRED DRSFRMACBS (SF), DRCRELEXFACBS (CRE) | Market Timing |
| Eviction filings | **BROWSER** + parcel | county courts (eCourts, browser-only); also an Owner-Distress parcel signal | both |

### Deal-level (what kills returns)
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Cap rate vs cost of debt | **WIRED** (debt) / **PAID** (cap) | cost of debt from FRED; cap rates from comps or CoStar | Underwriting |
| Insurance & tax-reassessment trajectory | **OURS** (tax) / **PAID/NO** (insurance) | parcel `tax_year`/assessed-value history gives reassessment; insurance is proprietary | Underwriting |
| DSCR at exit refi | compute | pro-forma input (use forward rate assumptions) | Underwriting |
| Expense ratio vs submarket norm | **PAID**/partial | needs operating comps | Underwriting |

## Batch 2

### Capital-markets plumbing
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| SLOOS net % tightening CRE | **WIRED** | FRED DRTSCLCC (+ DRTSCILM C&I) | Market Timing |
| CMBS delinquency & special servicing | **PAID** | Trepp | Market Timing |
| Loan maturity walls by vintage | **PAID** | Trepp / MSCI | Underwriting |
| Bank CRE concentration ratios | **FREE-ADD** | FDIC BankFind API (free, per-bank call reports; involved) | Market Timing |

### Transaction-side
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Cash share / investor share by metro | **FREE-ADD**/partial | Redfin investor-purchase data | Market Timing |
| Bid-ask persistence (withdrawal/fallout) | **FREE-ADD** | Redfin (withdrawn/pending-to-closed) | Market Timing |
| Appraisal-gap frequency | **PAID** | MLS / CoreLogic | Underwriting |

### Local / parcel
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Permit type mix (repair vs new) | **OURS** | our permit signals carry `type_of_work` (New/Alteration/Demolition…) — compute the mix directly | Market Timing |
| Zoning / entitlement pipeline, upzoning | **OURS** | we already pull rezoning + zoning-case sources | Market Timing + parcel |
| Assessment-appeal volume | **BROWSER** | county assessor / Board of Equalization (portal) | Market Timing |
| Rental listing velocity & concessions | **FREE-ADD**/partial | Apartment List (free-ish), MLS for SFR | Market Timing |
| Utility connections / disconnections | **NO** | utility-held, privacy-restricted (would be the best occupancy truth) | — |
| Insurance carrier withdrawals | **FREE-MANUAL** | state DOI announcements / news (not an API) | Underwriting |

### Structural (slow, decisive)
| Indicator | Verdict | Source / note | Model |
|---|---|---|---|
| Property tax reassessment cycles | **OURS** + schedule | parcel `tax_year` + county reassessment calendar | Underwriting |
| Household formation vs population | **FREE-ADD** | Census ACS (annual) | Market Timing |
| School district ratings / boundary shifts | **FREE-ADD** | state DOE report cards (free); GreatSchools (paid) | Market Timing |

## What this means

- **Gatherable now or cheaply**: the whole rates/credit/supply/demand core of both
  lists is free (FRED) and mostly wired here; Redfin/Zillow/Census/FDIC add the
  rest of the market layer with the same download-and-parse pattern.
- **Already ours**: permit type-mix and the zoning/entitlement pipeline fall out of
  data MonitorCLT already sources — no new provider.
- **Paid**: the CRE-specific plumbing (Trepp CMBS/maturity walls) and granular
  transaction/appraisal data. Worth it only if CRE is a real strategy.
- **Off-limits**: utility connect/disconnect (the one everyone wants).

The founder's caveat governs the build: **a handful measured monthly at a
consistent geography beats forty measured once.** So this module wires a tight,
verified core and leaves the long tail as documented, opt-in additions — not a
sprawl of one-off pulls.
