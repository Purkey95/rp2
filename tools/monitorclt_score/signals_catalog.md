# Signal catalog — the process to track and identify

Every signal the model scores, grouped by its dimension, with **where the data
comes from** and **how obtainable it is**. This is the tracking registry: to
"turn on" a signal, wire its source to emit that `signal_type` into the `signals`
table — the score picks it up automatically (weight + dimension already set in
`weights.json`).

Availability codes:
- **WIRED** — a sourcing adapter already emits it (or can today).
- **DERIVED** — computed from our parcel/enrichment data (no new source).
- **FREE-ADD** — free API/file, same adapter pattern, not yet wired.
- **SOS** — Secretary of State business registry (NC SoS search; often free/bulk).
- **ROD** — Register of Deeds; verified **browser-only** in all 8 counties (needs `browser_api`).
- **COURT** — Clerk of Court / eCourts; **browser-only** (Odyssey/portal).
- **ENV** — environmental records (EPA/FEMA/state); mostly free APIs.
- **MLS** — licensed MLS data (Canopy) or a paid aggregator.
- **UTIL** — utility-held; not obtainable at scale (privacy).
- **NEWS** — announcements/manual (no API).

## Financial Distress
| Signal | Source | Avail |
|---|---|---|
| tax_delinquency, tax_sale, foreclosure | county tax / GIS | **WIRED** |
| notice_of_default, substitution_of_trustee, lis_pendens | Register of Deeds | ROD |
| hoa_lien, municipal_lien, mechanics_lien, tax_lien, judgment_lien, multiple_liens | Register of Deeds | ROD |
| balloon_maturity, hard_money_maturity, private_mortgage, seller_financed, second_mortgage, heloc_investment, high_ltv, cash_out_refi_recent, cross_collateralized, assignment_of_dot | Register of Deeds (deeds of trust) | ROD |
| judgment_after_mortgage, bankruptcy | courts / ROD | COURT |
| high_equity_proxy | parcel value − recorded mortgage | DERIVED (needs ROD mortgage for exact) |

## Property Distress
| Signal | Source | Avail |
|---|---|---|
| code_violation, nuisance, housing_violation, unsafe_structure, boarding_order, stop_work_order, repeat_code_inspection, overgrown_lot, illegal_dumping, pest_citation, illegal_occupancy, junk_vehicle | county/city code enforcement | **WIRED** (Meck, Lincoln, Iredell) / FREE-ADD elsewhere |
| demolition_permit, unpermitted_work, roof/electrical/plumbing/hvac_violation, renovation_permit | permits | **WIRED** (permit feeds) |
| failed_inspection, fire_marshal_violation | permits / fire marshal | WIRED / FREE-ADD |
| vacancy | vacancy registry / parcel flag | **WIRED** (partial) / DERIVED |
| septic_major_repair, sewer_lateral, backflow_violation, stream_buffer_violation, erosion_control, environmental | env-health / permits | **WIRED** (Iredell OSWP, erosion) / FREE-ADD |
| ust_issue, contaminated_site, brownfield | EPA (FRS/ECHO), state | ENV (free) |
| fema_repetitive_loss | FEMA/NFIP | ENV (free-ish) |
| fire_damage, storm_damage, foundation_complaint, retaining_wall_failure | fire dept / code / news | FREE-ADD / NEWS |
| lead/asbestos/mold_remediation | permits / state env | FREE-ADD |

## Ownership Transition
| Signal | Source | Avail |
|---|---|---|
| probate, estate_low_cash, estate_owner_after_death, deceased_tax_owner, guardianship_sale, partition_action, quiet_title | Clerk of Court (probate/civil) | COURT (+ obituaries pipeline for death) |
| divorce, heir_interest_transfer, spouse_removed_title | courts / ROD | COURT / ROD |
| inherited_recent, transfer_1dollar, quitclaim_relatives, trust_transfer_in/out, llc_to_member, related_llc_transfer, ownership_pct_change, fractional_owners | Register of Deeds (deed analysis) | ROD |
| business_dissolution, llc_admin_dissolution, foreign_llc_withdrawal, registered_agent_resignation, corporate_ownership_change | Secretary of State | **SOS** (free search / bulk) |
| absentee, out_of_state, long_tenure_20y | parcel mailing vs situs / sale date | **DERIVED** |
| mailing_address_changed, tax_bill_undeliverable, po_box_other_state, nursing_facility_address | assessor / parcel mailing | DERIVED (needs mailing-history or a nursing-facility address list) |

## Landlord Fatigue
| Signal | Source | Avail |
|---|---|---|
| eviction, repeat_eviction, tenant_lawsuit, security_deposit_lawsuit, smallclaims_vs_landlord | Clerk of Court | COURT |
| rental_license_expired/suspended, failed_rental_inspection, tenant_housing_complaint, hoa_violation_rental | city rental registration / code | FREE-ADD / WIRED (code) |
| rental_vacancy_90d, rental_advertised_repeatedly, rental_price_reduced, rental_to_sale_listing | rental listings | MLS / FREE-ADD (Apartment List, Zillow) |
| pm_change, pm_termination | management records / listings | NEWS / MLS |
| selling_sequential, aged_portfolio_no_acq, llc_to_individual_transfer, large_portfolio | **entity resolution over deed transfers** | **DERIVED** (portfolio) + ROD |

## Disposition Probability
| Signal | Source | Avail |
|---|---|---|
| recently_sold_another, portfolio_liquidation | entity resolution over transfers | **DERIVED** (built) |
| expired_mls, back_on_market, contract_terminated, multiple_contract_failures, price_reduction_after_failed, withdrawn_after_inspection, relisted_different_agents, listed_180d | MLS listing history | MLS |
| auction_failed_reserve, foreclosure_postponed_repeatedly, probate_sale_fell_through, tax_foreclosure_redeemed | courts / auction / tax office | COURT / FREE-ADD |
| dumpster_permit, new_roof_presale, interior_renovation_after_long_ownership, estate_cleanout | permits / activity | **WIRED** (permit type + tenure) / NEWS |
| utilities_activated_after_vacancy | utility | UTIL (not obtainable) |
| mortgage_payoff | Register of Deeds (satisfaction) | ROD |
| development_case, rezoning | zoning/planning | **WIRED** |

## What this tells you about coverage

- **On now** (WIRED + DERIVED): the entire county-GIS distress core (tax, code,
  permits, vacancy, zoning) plus every ownership-attribute and behavioral signal
  we compute from parcel/entity data. That already fills Property Distress, most
  of Ownership Transition, and the behavioral half of Disposition.
- **One credential/records-request away**: **SOS** business-entity signals (free),
  **ENV** records (free), and **FREE-ADD** rental/listing-adjacent feeds.
- **Browser-only public records** (the big remaining blocks): **ROD** (all liens,
  lis pendens, deed-anomaly transfers, mortgages/LTV) and **COURT** (probate,
  divorce, bankruptcy, eviction, partition) — the `browser_api` frontier, starting
  with Gaston CCS.
- **Paid**: MLS listing/failed-transaction history (the Disposition dimension's
  richest inputs).
- **Not obtainable**: utility connect/disconnect.

The model scores whatever is present and shows *which dimensions are missing data*
for a parcel — so coverage gaps are visible, never silently zero.
