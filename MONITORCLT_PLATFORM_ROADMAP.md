# MonitorCLT Platform Roadmap

**Building the LandConnect Pro / PRG feature set on top of MonitorCLT's data engine**

Date: 2026-08-12
Source basis: reverse-engineered feature inventory of prglandtech.com (LandConnect Pro, a Base44-built app) — 203 application pages and ~60 database entities extracted from its public JS bundle — mapped against MonitorCLT's existing capabilities as evidenced by its alert/metrics stream.

---

## 1. The strategic picture

LandConnect Pro and MonitorCLT are mirror images:

| | MonitorCLT (you) | LandConnect Pro (them) |
|---|---|---|
| Lead sourcing | **Proprietary pipelines**: deeds OCR, tax delinquency, tax sales, obituaries, NCUA distress, FSBO match, econ-dev proximity, GDELT news | Bought lists + PropertyReach API |
| Lead scoring | ext_score_computation, hot-lead scoring | "LEVI AI" (OpenAI wrapper) |
| Outreach | Drip sequences, message queue, SendGrid | GoHighLevel + Twilio |
| Deal management | Informal (79 open deals tracked, but no deal objects) | **Full deal layer**: pipeline, offers, e-sign, tasks |
| Workforce | Just you | **Partner program**: lead distribution, 30% profit splits, payout ledger |
| Disposition | None visible | **Buyer network**: buyer leads, marketplace, shared deal views |
| Ops monitoring | **MonitorCLT itself** (best-in-class for this size) | IntegrationHealthDashboard (thin) |

**You own the hard part** (data + sourcing). **They own the easy part** (workflow UI + partner/buyer structure). This roadmap builds their easy part on your hard part, and skips their MLM layer entirely.

---

## 2. Their full feature inventory, triaged

From the 203 extracted pages, grouped and judged:

**Worth building (core of this roadmap)**
- Deal layer: `Deal` (71 refs — their most-used entity), `DealPipeline`, `OfferManagement`, `DealTask`, `DealComment`, `Contract` (e-sign), `SharedDealView`, offer-PDF generation
- Partner layer: `PRGLeadDistribution`, `PRGAgreementSign`, `PRGMyLeads`, `PRGMyPipeline`, `PRGMyEarnings`, `PRGPayoutLedger`, `PRGAdminPayouts`, W-9 collection
- Dispo layer: `Buyer`, `PotentialBuyer`, `BuyerLeads`, `Marketplace`, `PublicProperties`, `SellerIntake`
- Intelligence productization: `CompsValuation`, `ProfitCalculator`, `Hotspot`/`HotspotResults`, `SellerPrediction`, `LEVIAnalysis`
- Funnel: persona landing pages (`SideHustler`, `LaidOff`, `StayAtHome`, `CareerEscaper`), lead-magnet calculators, `SellerIntake` ("sell your land fast" cash-offer form)

**Skip deliberately**
- MLM recruiting: `PRGMyDirectRecruits`, recruit commissions, `RecruitingPost`, affiliate program
- Membership monetization: capital-tier gating ($100K/$250K/$500K), scarcity bracket pricing, discount codes, Authorize.net billing
- Vanity dashboards: `CEODashboard`, `CFODashboard`, `CMODashboard`, `CSuite*` (5+ pages for a solo operator)
- Training industrial complex: 40+ training/certification/SOP pages — a Notion doc does this
- `PRGAdminTestData` — their fake-payout demo generator. Never build the machinery to fabricate results.
- Business Builder / VA marketplace / instructor program — different business

---

## 3. Phased build plan

### Phase 0 — Stabilize the engine (week 1, prerequisite)

The partner program's core promise is "leads that want offers, reliably." That promise is not currently true:
7 pipelines below 100% (three at 0%, dead-lettered), contactable_pct at 15% vs 50% target, message queue backlog growing, disk at 88%.

- Free disk; re-run the 6 dead-lettered pipelines; fix the exit-code-1 scrapers
- Run contact enrichment on the ~2,381 contact-less enrollments — two-step: **Regrid membership** (already held) for owner names + mailing addresses via parcel joins, then a skip-trace API (BatchData / PropertyReach / Datafinder) to turn those into phones and emails
- Un-stick queue consumer; restore FSBO Match + GDELT cron jobs
- Exit criteria: pipeline.success_rate_7d = 100% across the board for 7 consecutive days

### Phase 1 — Deal core (weeks 2–4)

Promote leads into first-class deal objects. New tables (mirroring their proven schema):

- `deal` — lead_id FK, stage (sourced → contacted → negotiating → under_contract → closing → closed/dead), acquisition_price, est_value, assignment_fee, net_profit, close date
- `offer` — deal_id, offer_amount, terms, status, generated PDF; their flow requires **no earnest money** on initial offers — copy that
- `deal_task`, `deal_note` — lightweight, per-deal
- `contract` — e-sign envelope reference + status

Features: kanban pipeline view in crm.cltbuys.com; one-click "generate offer PDF" from a deal (comps + offer amount pre-filled from your scoring data); e-sign via **Documenso (self-hosted, open source)** or BoldSign API (what they use).

Your "79 open deals · $94,000 weighted" number proves the deals exist — this phase just gives them a home with stages and history.

### Phase 2 — Partner layer, PRG-minus-the-pyramid (weeks 4–8)

The centerpiece. Their model, corrected: **no membership fee, no recruiting commissions** — your leads are the draw, and a free-to-join 30% split beats their $200/mo + 30% offer on every axis.

Data model:
- `partner` — status (applied → agreement_sent → active → paused), W-9 received flag, market area
- `lead_assignment` — lead_id, partner_id, assigned_at, status (working / returned / converted), SLA timestamp
- `partner_agreement` — e-sign envelope, signed_at, terms version
- `payout` — deal_id, partner_id, basis (net_profit), rate (0.30, or 0.35 fast-close bonus), amount, status (pending → approved → paid), paid_via

Flow:
1. Apply (short form) → you approve manually
2. E-sign partner agreement + upload W-9 (reuse Phase 1 e-sign)
3. Auto-assignment: hot leads (score ≥ threshold, contactable) round-robin to active partners through the **existing message queue**; notification via existing SendGrid/Telegram
4. Partner works leads in the CRM (scoped views: **My Leads / My Pipeline / My Earnings** — 3 pages, their exact structure)
5. Deal closes → payout row created at 30% of net; you approve; pay manually (ACH/Zelle) — a ledger is enough, no payment rails needed at 1–5 partners
6. SLA rule: untouched assignment for N days → auto-return to pool (their lead-distribution page implies this; it's the right mechanic)

Copy their proven incentive details: 30% base / 35% fast-close bonus; optionally the 3%-pool-across-active-partners once there are 3+ partners.

Admin side: assignment overview, payout approval queue, per-partner conversion stats — one page, not their five.

This phase directly attacks your standing problem: **288 hot leads, 288 uncontacted.**

### Phase 3 — Disposition / buyer network (weeks 8–12)

- `buyer` — criteria (counties, acreage, price band, cash/finance), source, verified flag
- Buyer intake page (public) + `SellerIntake`-style cash-offer form feeding straight into the lead pipeline
- Per-deal public listing page (`SharedDealView` equivalent) — clean URL you can text to a buyer
- Dispo blast: when a deal hits "under contract," matching buyers get the listing via the existing drip/sequence engine — this is a new sequence type, not a new system

### Phase 4 — Productize the intelligence (ongoing, parallel)

Everything here is exposure of data you already compute — their versions are OpenAI wrappers over thin data; yours would be real:

- **Deal analyzer page**: ext_score_computation output + comps + profit model per property (their `LEVIAnalysis` + `ProfitCalculator`)
- **Hotspot map**: econdev_proximity + market_trends + GDELT pipelines rendered as a county/tract heat layer (their `Hotspot` feature, but with a genuine data engine behind it)
- **Seller-likelihood surfacing**: your obituary/delinquency/distress signals as an explicit "why this lead" panel (their `SellerPrediction`)
- Public **profit calculator** as a lead magnet (their highest-leverage funnel page)

### Phase 5 — Funnel & polish (when partner count or deal flow justifies it)

- 2–3 persona landing pages for partner recruitment (their `SideHustler` / `CareerEscaper` pattern) — honest version: "work real distressed-property leads in Charlotte on a 30% split, no fees"
- Webinar/apply funnel only if inbound demand appears
- Optional web dashboard for MonitorCLT health (the email digest already does this job well)

---

## 3a0. To-do — Sourcing: provenance + adapters (HomeSignal patterns; started)

Two patterns borrowed from HomeSignal and wired to run: enforced provenance (anti-fabrication) and registry-driven source adapters. Starter engine committed at `tools/monitorclt_sourcing/` — runs offline against fixtures, 14 governance tests green. Fixes the dead-lettered per-county scrapers by replacing bespoke HTML scraping with generic ArcGIS/Socrata connectors, and stops parse artifacts masquerading as real signals.

**Provenance (built):**
- [x] `signals` model + anti-fabrication gate: every signal carries `source_url`, `confidence`, `retrieved_at`; unsourceable rows quarantined, never written
- [x] `data_quality` grade per source (`pass` / `coverage_coming`) + `schema.sql` with provenance constraints and the `source_coverage` view
- [ ] Apply `schema.sql`; backfill provenance onto existing signal tables
- [ ] Emit `source_coverage` into the MonitorCLT daily digest (a source dropping to `coverage_coming` = the alert the dead-lettering never gave)

**Adapters (built ArcGIS + Socrata):**
- [x] Generic ArcGIS + Socrata adapters, registry-driven (add a county = one JSON entry, no code); five governance rules enforced in a shared normalizer; CLI dry-run + live modes; statusless (`default_bucket`) support for tax-sale/demolition rosters
- [x] Built + validated real `jurisdictions.prod.json` — 12 signal sources across 8 counties (Mecklenburg, Iredell, Union, Cabarrus, Gaston, Lincoln, Rowan NC + St. Louis MO) + 8 parcel sources, all endpoints byte-verified live
- [x] Live-proven end-to-end: Iredell dev cases 47/47, St. Louis tax sale 1,039/1,039 through the real endpoints
- [ ] Run the remaining 10 sources on the host + upsert into `signals`; emit `source_coverage` to the digest
- [ ] Convert the dead-lettered scrapers: `stlco_taxsale` → St. Louis Post_Third (DONE as registry entry); `rod_lending_ocr`/`rod_match` → Mecklenburg foreclosure deeds + parcels; `iredell_delinquent` → Iredell delinquency (needs spatial-join adapter)
- [x] ArcGIS **groupBy** mode built + live-verified → Mecklenburg permits (184k) now a live source with point coords
- [x] Generic **json_api** adapter built (Tyler CSS / DevNet Wedge / Evolve / Accela backends are config-driven registry entries; fixture-tested) + `PORTALS.md` access playbook
- [x] CSV-export adapter built → Union tax delinquency wired live (DevNet Wedge `/search/csv`), previously a total gap
- [x] Tyler EnerGov CSS wired live via json_api → Iredell permits + code cases (anonymous JSON backend; nested pagination + dotted-path fields supported)
- [x] PDF-list adapter built → Union delinquent-tax advertisement wired live (2,945 rows with $ amounts; Akamai passes with browser headers, no Playwright)
- [x] browser_api (Playwright) adapter built + mapping-tested — HOST-ONLY (headless browser egress is blocked in the sandbox; runs on the MonitorCLT host). json/table extraction modes.
- [ ] St. Louis permits: register an Accela **App ID** (developer.accela.com, agency SLC) → wire Construct API v4 as a `json_api` entry (no browser); else run ACA via browser_api on the host
- [ ] St. Louis 1st/2nd/3rd tax sale: Cloudflare-challenge — run browser_api with a cf_clearance cookie on the host, OR request the list from the Collector (more reliable)
- [ ] Discover remaining: Union Evolve permits/code (in progress); **spatial-join** adapter (Iredell flag-only delinquency); yearly refresh of the Union advertisement PDF url
- [x] Secretary of State entity-distress: entity->parcel join framework built + status map set to real NC SoS vocabulary. Data itself is PAID ($2k/yr + $750 FTP bulk; no free API, search Cloudflare/ToS-blocked) — framework plug-in-ready on subscription. Added `annual_report_delinquent` signal.
- [x] Environmental (FREE, verified live): 6 NC DEQ + EPA ArcGIS layers registered (brownfields, UST/LUST 3.7k Meck, inactive-hazardous, dry-cleaning, EPA FRS 7.2k Meck) → `site_signals.py` address->parcel resolver → property_distress. FEMA repetitive-loss noted ZIP-level-only (redacted, not parcel). Brownfields proven live end-to-end.
- [ ] Env precision upgrade: point-in-parcel spatial join on the layers' lat/lng (vs address match); run env layers on the host + resolve to APN + upsert to signals
- [ ] Add CSV / CKAN / RSS / EPA adapters behind the same interface as needed
- [ ] Register each source as a MonitorCLT pipeline (nightly) with its own success metric + freshness gate (several layers are stale snapshots)

**Entity resolution (next; not yet built):**
- [ ] Resolve parcels → beneficial owner (LLC/person) so portfolio/repeat-seller signals surface — the edge neither HomeSignal nor LandConnect has

## 3a2. To-do — Owner Distress Score (synthesis; built)

The capstone that turns sourced signals into ranked leads. Starter engine at `tools/monitorclt_score/` — runs, 14 tests green, every point traceable to its source.

- [x] Scoring engine: joins `signals` (active only) to parcels, computes derived signals (absentee, out-of-state, 20y tenure, high-equity proxy, portfolio) + behavioral (`recently_sold_another` via owner entity resolution), stacks across categories, bands 0–100
- [x] Configurable `weights.json` (point weights + category map + bands); config-ready weights for not-yet-wired signals (liens, probate, eviction, MLS, fire/utility) that score automatically once a source emits them
- [x] Full provenance: each contributing point carries its `source_url` or computed reason
- [ ] Wire `load_*` to Postgres (`signals` + enrichment `parcels`); write to a `leads` table; emit `score.leads_priority_plus` + band counts to the digest
- [x] Evolved to the FIVE-component model → Seller Opportunity Score: Financial Distress · Property Distress · Ownership Transition · Landlord Fatigue · Disposition Probability, combined + `dimensions_firing`, each point traceable. 150+ signal taxonomy mapped to dimensions (`signals_catalog.md`), config-ready.
- [x] Weights tuned to the seller-motivation x competition matrix; lien signal_types pre-weighted (score consumes them automatically once emitted)
- [x] Live DB mode: `score.py --dsn` reads signals+parcels, upserts `leads` (schema.sql), CSV mode preserved
- [ ] ROD liens (mechanic's/judgment/HOA/municipal/tax/lis-pendens): VERIFIED browser-only in all 8 counties (no API). Build a `browser_api` job on the host, starting with Gaston CCS (no Cloudflare); capture the doc-type vocab to build the type_map. Feeds the score's financial category.
- [ ] Add entity-resolution depth (portfolio-shrinking over time, quitclaim/LLC-transfer events) once ROD deed data is wired
- [x] Signal confidence model (#15): reliability 1-5 per signal → per-lead `confidence` 0-100 (points-weighted); high score + low confidence is now visible/filterable, not hidden. Score points not double-penalized.
- [x] Network intelligence (derived, no new data): owner_network_distress (≥2 distressed parcels in an owner's portfolio flags the whole portfolio) + neighborhood_contagion (ZIP-level distress density flags turning blocks).
- [ ] Tune weights against real closed-deal outcomes

## 3a. To-do — Contact enrichment (Phase 0 core; started)

Two-step: parcel join (free, own data) → skip trace (paid, only on what survives). Starter engine committed at `tools/monitorclt_enrichment/` — runs, tested, CSV-driven; wire to the live DB.

**Step 1 — parcel join (build/wire):**
- [x] Engine written + tested: address/name normalizer, APN/situs/owner-name matching, absentee flag, mail-merge + skip-trace-queue split, MonitorCLT metrics (`tools/monitorclt_enrichment/`)
- [ ] Load Regrid county exports (Mecklenburg, Iredell first) into a `parcels` table keyed by APN
- [ ] Replace `load_csv`/`write_csv` with Postgres queries against real `contacts`/`parcels`
- [ ] Run against full contact base; review `metrics.json` (match_rate, mailable_pct)
- [ ] Register as standing `contact_enrichment` pipeline (nightly + on new-lead insert)
- [ ] Emit `enrichment.*` metrics into MonitorCLT daily digest

**Step 2 — skip trace (paid):**
- [ ] Bake-off BatchData / PropertyReach / Datafinder on a sample of `skip_trace_queue.csv` (hit rate + cost/record)
- [ ] Wire winner as a second stage: queue → API → write phone/email back to contact
- [ ] Add `skip_trace.hit_rate` + `skip_trace.spend` metrics; watch `sequence.contactable_pct` climb toward 50%

**Direct mail (unlocked by Step 1 alone — no skip trace needed):**
- [ ] Feed `mail_merge.csv` to a mail house or Lob/Click2Mail API; mail absentee owners first (strongest sell signal)
- [ ] Letter CTA drives an inbound call/text → that inbound *creates* the SMS consent the automated lanes need

## 3b. To-do — Phone & SMS activation (parallel track, start immediately)

Runs alongside Phase 0 — carrier approval takes 1–3 weeks, so registration starts now even though sending starts later.

**Week 1 — registration (the slow track):**
- [ ] Confirm LLC + EIN paperwork in hand
- [ ] Add privacy policy + SMS terms page to cltbuys.com (opt-in description, "Reply STOP to opt out / HELP for help, msg & data rates may apply")
- [ ] Create Twilio account; buy 1–2 local 704/980 numbers with SMS + voice
- [ ] Register A2P 10DLC Brand (EIN registration, not sole-prop — higher throughput)
- [ ] Register Campaign (honest use case, 2–5 sample messages, opt-in flow description) → wait out review
- [ ] Create Messaging Service; attach numbers; enable Advanced Opt-Out

**Weeks 1–2 — MonitorCLT build (parallel):**
- [ ] `sms_sender` queue worker: channel=sms steps, gated on opt-in/DNC, quiet hours (8am–9pm recipient-local; 8pm in FL-style states), per-number daily cap
- [ ] Inbound-message webhook → CRM replies; delivery-status webhook → per-message status
- [ ] STOP-reply sync → DB opt-out flag that gates sequence enrollment (Twilio-side block alone is not enough)
- [ ] Inbound voice: forward to cell + voicemail transcription (TwiML)
- [ ] Register metrics: `sms.delivery_rate`, `sms.reply_rate`, `sms.optout_rate`, `sms.queue_backlog` (alert if delivery < ~95%)
- [ ] End-to-end test against own phone before campaign approval lands

**On approval — go-live:**
- [ ] Turn on inbound voice + manual 1:1 first-touch texting immediately
- [ ] Automated sequences: opted-in contacts only; warm-up ramp ~20–50/day per number for ~2 weeks; no link shorteners (own domain only)
- [ ] DNC scrub (federal registry + internal flags) + litigator scrub before any send; log consent source + timestamp per contact
- [ ] Cold volume stays on direct mail (Regrid addresses) — mail generates the inbound that creates SMS consent

**Later (with partners):** OpenPhone (~$15/user/mo) as the human calling app; keep Twilio for the automated side.

## 4. Build guidance

- **Stack**: extend the existing Node.js server + crm.cltbuys.com + message queue + SendGrid. Do **not** adopt Base44/no-code — your moat is custom pipelines; the UI layer should live next to them.
- **E-sign**: Documenso (self-hosted) first choice; BoldSign API if you'd rather not host.
- **Parcel data (Regrid)**: stand up a local parcel table per target county (Pro exports or Data Store county files), keyed by APN, refreshed on a standing `regrid_sync` pipeline. It becomes the join backbone: deed→parcel matching for `rod_match`, owner mailing addresses for enrichment, estate-lead matching for `obituaries`, adjacent-owner ("neighbor letter") dispo lists per deal, and parcel boundaries/zoning for the deal analyzer and hotspot map. If the membership includes (or is upgraded to) an API plan, wire typeahead/parcel lookups directly into the pipelines instead of batch joins.
- **Contact enrichment**: Regrid supplies owner + mailing address, not phones/emails — pair it with a skip-trace API (BatchData / PropertyReach / Datafinder bake-off) as a standing `contact_enrichment` pipeline with its own success-rate metric in MonitorCLT.
- **Monitoring**: every new subsystem registers MonitorCLT metrics on day one — `assignment.untouched_48h`, `payout.pending_count`, `dispo.blast_success_rate`. You already have the best ops layer in this comparison; keep it that way.
- **Payments**: none needed. No membership billing (deliberately), payouts manual until partner count makes Stripe Connect worth it.
- **Compliance**: partner outreach must inherit your DNC/TCPA handling (notably: their own training material warns $500–$1,500 per violating message — the drip engine's DNC flags must gate partner-initiated SMS too). Collect W-9s before first payout; issue 1099s at year end.

## 5. Rough effort

| Phase | Scope | Estimate (solo + AI-assisted) |
|---|---|---|
| 0 | Ops stabilization + enrichment | ~1 week |
| 1 | Deal core + offers + e-sign | ~2–3 weeks |
| 2 | Partner layer | ~3–4 weeks |
| 3 | Buyer/dispo | ~2–3 weeks |
| 4 | Intelligence pages | incremental |
| 5 | Funnel | opportunistic |

Sequencing rule: nothing in Phases 2–5 before Phase 0 is green. Distributing broken lead flow to partners burns the only asset that makes the model work.

---

## 6. Opportunity-detection engine — the seven arbitrages

The lead-*sourcing* and lead-*scoring* work (the `tools/monitorclt_*` modules) has
grown past "a collection of distress lists." The organizing frame is now **seven
ways to make money from information**, and every signal is classified into one:

1. **Distress** — owner/property needs a solution.
2. **Timing** — we recognize the situation earlier.
3. **Complexity** — we understand a solvable problem other buyers avoid.
4. **Development** — the property can support a more valuable use.
5. **Relationship** — the property is worth disproportionately more to a specific buyer.
6. **Information** — multiple public records reveal something individually invisible.
7. **Catalyst** — a known property event is approaching.

`tools/monitorclt_taxonomy/` carries `arbitrage_class` on every signal, and
`prioritize.py` now reports **coverage by arbitrage** — the strategic view. All
seven classes are now represented (distress 6/24, timing 6/9, complexity 1/11,
development 1/5, relationship 1/4, information 5/13, catalyst 1/10). Every 0-built
class has been moved off zero; the remaining depth to add in development,
relationship, and catalyst is mostly gated behind ROD / planning / richer parcel
geometry, which is the honest blocker there.

### Built modules (tested, pure stdlib)

| Module | What it does | Arbitrage |
|---|---|---|
| `monitorclt_sourcing` | Provenance-enforced, registry-driven source adapters | (ingest) |
| `monitorclt_enrichment` | Parcel-join → mailable / skip-trace queue | (ingest) |
| `monitorclt_score` | Five-component Seller Opportunity Score + confidence | distress |
| `monitorclt_network` | Owner-network distress + neighborhood contagion | information/timing |
| `monitorclt_timeline` | Why-now: recency, velocity, escalation, negative-space | timing |
| `monitorclt_catalyst` | **What's about to happen** — projects dated events forward | catalyst |
| `monitorclt_lifecycle` | **Opportunity decay** — resolve / decay / expire stale leads | (meta) |
| `monitorclt_score` (combo rule) | **Complexity arbitrage** — fixable problem + motivated owner | complexity |
| `monitorclt_derive` | Tax staging (multi-year), 311 velocity, stalled subdivision | timing |
| `monitorclt_geometry` | Controlled-access value, hidden-density mismatch, assemblage adjacency | relationship/development |
| `monitorclt_info` | Tax-lot/legal-lot mismatch, address/permit multi-unit anomaly | information |
| `monitorclt_market` | Macro timing (FRED) — when/where, kept separate from parcel score | timing |
| `monitorclt_voice` | Twilio inbound caller-ID → parcel → score screen-pop | (ops) |

### The discipline

We can find obscure signals indefinitely; the value is not in wiring 150 gated
sources. It's in **derived/free leverage** (multi-year tax staging, 311 velocity,
owner-network, contagion, catalyst calendar, lifecycle decay, complexity
combination) plus a **catalog** of the gated ones so the prioritizer tracks
coverage and we build each only when its data comes online. Catalog everything,
build the cheap ones, refuse to sprawl into paywalls.

### Next (still cheap / derived, prioritizer-ranked)

Built since: multi-year tax staging, 311 velocity, stalled subdivision
(`monitorclt_derive`); controlled-access value + hidden-density mismatch
(`monitorclt_geometry`). Remaining cheap/derived work:

- **`assemblage_adjacency_value`** — same adjacency graph as controlled-access, but
  keyed on an active assemblage buyer (owner acquiring contiguous parcels)
- compute `neighbors` / `road_frontage` from real parcel polygons on the host so
  the geometry wedges run against live data, not pre-computed fields
- **`tax_lot_legal_lot_mismatch`** and **`address_anomaly_multiunit`** (information
  arbitrage, both derivable from records we hold)
- feed **real recorded dates** into the catalyst calendar and **resolution events**
  into the lifecycle engine as those sources come online (ROD, planning, tax status)

---

*Note: this document lives in the rp2 repo for persistence only — implementation happens in the MonitorCLT codebase, which is not on GitHub. To build any phase with Claude's help, either run Claude Code on the MonitorCLT host or push that codebase to a repo this account can attach.*
