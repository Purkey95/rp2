# Wireframes — Seller Funnel & Operations Console

Blueprint §62–§63. Layout only; visual design is a separate pass.

## Seller funnel — five screens, mobile first

Every additional field must justify its cost in conversion. The funnel exists to
qualify, not to interview.

```
┌─ SCREEN 1 ────────────────────────┐   ┌─ SCREEN 2 ────────────────────────┐
│  Get a cash offer on your         │   │  Where can we reach you?          │
│  Charlotte home                   │   │                                   │
│                                   │   │  First name  [______________]     │
│  What property are you            │   │  Last name   [______________]     │
│  thinking of selling?             │   │  Mobile      [______________]     │
│                                   │   │  Email       [______________]     │
│  [ 123 Main St, Charlotte      ]  │   │                                   │
│                          ▸ Next   │   │                          ▸ Next   │
│                                   │   │                                   │
│  No obligation. No fees.          │   │  We text a code to confirm it's   │
└───────────────────────────────────┘   │  really you.                      │
        1 field. Nothing else.          └───────────────────────────────────┘

┌─ SCREEN 3 ────────────────────────┐   ┌─ SCREEN 4 ────────────────────────┐
│  Confirm your number              │   │  Tell us about the property       │
│                                   │   │                                   │
│  Code sent to ••• ••• 4471        │   │  When would you like to sell?     │
│                                   │   │   ( ) Within 30 days              │
│      [ _ ][ _ ][ _ ][ _ ][ _ ][ _ ]│   │   ( ) 1–3 months  ( ) Just looking│
│                                   │   │  Condition?                       │
│  Resend in 0:42                   │   │   ( ) Major repairs ( ) Some ( ) Good│
│                          ▸ Verify │   │  Why are you selling?  [ ▾ ]      │
│                                   │   │  What matters most?    [ ▾ ]      │
│  2 attempts remaining             │   │                        ▸ Finish   │
└───────────────────────────────────┘   └───────────────────────────────────┘

┌─ SCREEN 5 ────────────────────────────────────────────────────────────────┐
│  Thanks, Sarah — we have what we need.                                    │
│                                                                            │
│  A local buyer will call you at ••• ••• 4471 within one business day.     │
│  Reference: SE-4B29                                                        │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │ CONSENT (shown before submit, recorded verbatim)          [REVIEW F4] │ │
│  │ By submitting you agree we may contact you about your property and   │ │
│  │ may share your details with a vetted local buyer. You can opt out at │ │
│  │ any time. [Privacy] [Terms]                                          │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

**Screen 5's consent block is not decoration.** `onward_disclosure_shown` must
be `true` before an opportunity may ever reach a buyer, and the exact text shown
is hashed into `audit.consent_record`. See F4 in the blueprint review.

## Operations console — lead inbox

Scanned, not read. State is visible in form as well as number.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ LEAD INBOX          Today 42 new · 9 P0 · 4 review · 2 stale               │
│ [All] [P0] [Manual review] [Duplicates] [Stale data] [Rejected]            │
├──────┬─────────────────────┬────────┬───────┬──────────┬───────────────────┤
│ PRI  │ SELLER / PROPERTY   │ MASTER │ TRUST │ TIMELINE │ FLAGS             │
├──────┼─────────────────────┼────────┼───────┼──────────┼───────────────────┤
│ ▮P0  │ S. Johnson          │  94.3  │  100  │ <30 days │ 🔑 free&clear     │
│      │ 123 Main St, 28208  │        │       │          │ 🏚 absentee ⚖ fcl │
├──────┼─────────────────────┼────────┼───────┼──────────┼───────────────────┤
│ ▮P0  │ M. Alvarez          │  88.1  │   95  │ <30 days │ 🏚 absentee       │
│      │ 4670 Brooktree Dr   │        │       │          │                   │
├──────┼─────────────────────┼────────┼───────┼──────────┼───────────────────┤
│ ▨P2  │ TAYBILT HOMES LLC   │   ——   │  100  │ <30 days │ ⚠ STALE: distress │
│      │ 3007 Yada Ln, 28208 │ suppr. │       │          │ 11d · entity      │
├──────┼─────────────────────┼────────┼───────┼──────────┼───────────────────┤
│ ▨P2  │ D. Wright           │   ——   │   72  │ 1–3 mo   │ ⚠ AMBIGUOUS addr  │
│      │ 101 Lakeview Dr     │ review │       │          │ 3 condo matches   │
└──────┴─────────────────────┴────────┴───────┴──────────┴───────────────────┘
```

A suppressed score shows `——`, never `0`. Zero would sort to the bottom and be
read as "bad lead"; `——` reads as "we don't know yet", which is the truth, and
keeps it in the review queue where it belongs.

## Lead detail — the five blocks

Blueprint §48, with a sixth row the blueprint omitted: data freshness.

```
┌─ SELLER ──────────────────┐ ┌─ PROPERTY ────────────────────────────────┐
│ Sarah Johnson             │ │ 123 Main Street, Charlotte NC 28208       │
│ ••• ••• 4471  ✓ verified  │ │ SFR · 3/2 · 1,412 sqft · built 1961       │
│ Consent C_V1 ✓ onward     │ │ Owned 11 years · absentee                 │
└───────────────────────────┘ └───────────────────────────────────────────┘
┌─ MOTIVATION  92 ──────────┐ ┌─ INVESTMENT INTELLIGENCE ─────────────────┐
│ + timeline <30 days   +25 │ │ Market value      $269,400                │
│ + foreclosure reason  +20 │ │ Est. equity       $200,000  (conf. 0.82)  │
│ + major repairs       +10 │ │ Free and clear    yes                     │
│ + speed priority      +10 │ │ Foreclosure       notice filed 2026-08-22 │
└───────────────────────────┘ └───────────────────────────────────────────┘
┌─ ACTIVITY (from Follow Up Boss) ──┐ ┌─ DATA FRESHNESS ──────[REVIEW F3]─┐
│ 09:14 delivered                   │ │ property   FRESH   2d             │
│ 09:31 call attempt — no answer    │ │ ownership  FRESH   1d             │
│ 09:33 text sent                   │ │ financial  AGING   9d             │
│ —                                 │ │ distress   FRESH   0d             │
└───────────────────────────────────┘ │ market     FRESH   3d             │
                                      └───────────────────────────────────┘
```
