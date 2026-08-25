---
type: answer
created: 2026-08-25
updated: 2026-08-25
tags: [architecture, seller-engine, monitorclt, review, decision]
---

# Review: Seller Engine Technical Blueprint v2.0

**Question:** does the Seller Engine blueprint combine with the zero-budget
marketing strategy in [[marketing-strategy-monitorclt]]?

**Verdict: yes, but not as written — and the join is more valuable than either
piece alone.** The blueprint is the machine. The marketing strategy is fuel that
costs nothing. The blueprint currently assumes fuel you have to buy, and that
assumption is doing more damage to the plan than any of its technical choices.

## What the blueprint gets right

This is well-above-average architecture and most of the locked decisions should
stand. Specifically worth keeping, because each prevents a class of bug that is
expensive to unwind later:

- **Ledger, not a mutable balance** (§50). Correct, and the single most common
  thing teams get wrong in a marketplace.
- **Immutable score versions and enrichment snapshots** (§25, §32, §37). This is
  what makes "why did this score 92 in March?" answerable. Most systems cannot.
- **Deterministic rules before ML** (§33), and refusing to ship Deal Probability
  until labelled outcomes exist (§34). Disciplined, and rarer than it should be.
- **Never copy the property database into the lead system** (§1). Right call.
- **Correlation ID threaded from ad click to closing** (§8). This is the spine of
  the whole learning loop.
- **Atomic RESERVE → CHARGE → ASSIGN with a lock** (§44). Correctly identified as
  one of the most important details in the system.
- **Modular monolith over microservices** (§3). Right for this team size.
- **AI recommends before it acts** (§78), with schema-constrained outputs (§79).
- **§82, what not to build.** The most valuable section in the document.

## The collision

The blueprint's funnel begins:

```
Paid Ad → Landing Page → Seller Submission → OTP → ...
```

The strategy in [[marketing-strategy-monitorclt]] begins with published data,
press citations, and a free alert list — because the constraint was explicitly
low-to-no cost. These are not additive as written. The blueprint presumes an ad
budget, a media-buying function, and eventually a lead marketplace. That is a
legitimate business, but it is a *different* business, and stacking it on top of
the current operation without saying so is how the plan quietly changes shape.

## The synthesis

Swap the top of the funnel and everything downstream survives intact:

```
Data report → press citation → free alert list → landing page → Seller Submission → OTP → ...
```

`marketing.session`, `marketing.attribution_event`, and the correlation ID work
identically for organic sources — arguably better, since there is no platform
click ID to lose and no attribution window to argue about. The whole §14–15
attribution layer applies unchanged.

The reason this matters is §85, the economic validation gate. **Paid acquisition
cannot pass that gate at current volume; organic can.** If cost-per-inquiry is
near zero because the seller arrived from an Observer citation, the economics
close at a fraction of the volume paid traffic would demand. Build the machine,
prove the conversion rates with free fuel, and only then decide whether to buy
more.

## Five findings

### F1 — It builds an acquisition system on top of an activation failure

The blueprint has no equivalent of Phase 0. Today MonitorCLT holds **3,025 hot
leads with 302 never contacted**, and **2,381 of 2,804 drip enrollments (85%)
have no phone or email** (`sequence.contactable_pct` 15.09% against a 50%
target). The Seller Engine is an elaborate apparatus for acquiring *new* seller
inquiries while the existing ones sit unworked.

**Recommendation:** insert **G-minus-1** before G0. It does not gate on schema
design; it gates on contactability crossing 50% and the uncontacted-hot-lead
count reaching zero. If the team cannot work 302 leads, it will not work 302 more
arriving through a nicer pipeline.

### F2 — The blueprint does not appear to know Follow Up Boss exists

§4 and §83 route qualified opportunities into **Twenty CRM**. MonitorCLT already
runs **Follow Up Boss**, holding those 3,025 leads, with a live agent workflow,
appointments, and a Zillow relationship attached to it.

Nothing in eighty-seven sections mentions FUB. That omission suggests the
blueprint was drafted without full visibility into the running system, which is
worth weighing when deciding how much of it to accept unexamined. A mid-flight
CRM migration is a large unforced risk taken for no stated benefit.

**Recommendation:** keep Follow Up Boss as the system of engagement for v1.
`lead.opportunity` remains the system of record. Revisit Twenty only if FUB
demonstrably blocks something, and never during the pilot.

### F3 — Enrichment is assumed reliable; it demonstrably is not

§23 asks the MonitorCLT data platform for property, owner, deed, mortgage, lien,
tax, foreclosure, code, permit, vacancy, listing, valuation, comp, and distress
data as though those are dependable. As of 2026-08-24: `rod_lending_ocr` 0%,
`econdev_proximity` 0%, `code_enforcement_sync` 0%, `condo.declaration-pulls` 0%,
`fsbo_byowner` 38%, `osha_inspections` 25%. Four have been dark for 10–12 days.

§24's provenance model captures `source`, `observed_at`, `retrieved_at`, and
`confidence` — good — but **nothing in the design gates on staleness.** A stale
feed does not lower confidence; it silently keeps returning the last value it
had. So §25 faithfully snapshots stale data, §31 scores it, and the snapshot
records high confidence in a number that is eleven days rotten. The audit trail
would make that *reproducible* without making it *correct*.

**Recommendation:** add a freshness SLA per enrichment domain and a scoring
guard. If a domain required by a score is beyond SLA, the score is not emitted
as a number — the opportunity routes to `MANUAL_REVIEW` with reason
`STALE_ENRICHMENT`, naming the domain. This is a small addition that turns their
single most-proven operational failure into a caught error instead of a
laundered one.

### F4 — The compliance surface is larger than the document admits

§20 versions consent and §72 isolates PII. Both good, neither sufficient for what
this system actually does: **solicit distressed North Carolina homeowners and
resell their contact details to investors.**

Missing: TCPA and Do-Not-Call treatment of the OTP-verified phone (verifying a
number is not consent to be called by a third party); North Carolina's
foreclosure-rescue statute, **G.S. 75-120**, which governs solicitation of
homeowners in distress; and any notion of what a seller was told about onward
disclosure at the moment they submitted. §49's entitlement layer controls what
buyers *can* see; it does not establish what sellers *agreed* to.

**Recommendation:** this needs a North Carolina real estate attorney's review of
the consent text, the OTP flow, and the buyer disclosure — before G8, not before
G10. It is the cheapest risk reduction in the programme and the only finding here
that carries legal rather than technical consequences.

### F5 — Name the point where "zero cost" ends

OTP delivery (§19) is metered per message. Paid ads (§13, §83) are the entire top
of the funnel. Object storage, Airflow, Metabase, and Postgres all carry hosting
cost, on a box currently at 90% disk with 24.8GB free.

This is not an objection — it is a request that the number be stated. The
marketing strategy was built to a zero-budget constraint. The Seller Engine
quietly relaxes that constraint. Both can be true, but the transition should be a
decision someone makes, not a thing that happens.

## Recommended sequencing

| Window | Work |
|---|---|
| **G-1** | Phase 0: contactability to 50%, 302 hot leads worked, CAN-SPAM address set, `rod_lending_ocr` repaired, disk reclaimed |
| **G0** | Freeze schemas, events, API contracts — **with the freshness SLA and Follow Up Boss amendments folded in** |
| **G1–G7** | Build the funnel to receive *organic* traffic from the marketing pillars |
| **G8** | Pilot on organic only. No ad spend. This is the cheapest possible way to learn the conversion rates |
| **G9** | Economic validation — now answerable at low volume, because CAC is near zero |
| **G9.5** | *New gate:* only after organic conversion is proven, decide whether paid acquisition is worth buying |
| **G10** | External marketplace — unchanged, still gated, still probably no |

## On the 15 locked decisions

Endorse 1–14 as written, with two amendments: **#3** (leads reference existing
property entities) should additionally require the freshness gate from F3, and
**#15** (marketplace after internal validation) should have the paid-acquisition
decision inserted ahead of it as its own gate. Decision **#11** — outcome
tracking is mandatory, not optional CRM hygiene — is the one I would defend
hardest if anyone tries to cut it for schedule, because it is the only thing that
makes §86's loop close.

Related: [[marketing-strategy-monitorclt]] · [[monitorclt]] · [[agentic-harness-assessment]]
