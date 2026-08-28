---
type: answer
created: 2026-08-28
updated: 2026-08-28
tags: [calendar, real-estate, diagnosis]
---

# Why the Charlotte REIA Calendar Import Is Stuck

The "North Carolina real estate groups" session (08-26) recorded that event
import and connector writes were both failing, and proposed either importing
the `.ics` by hand or starting a fresh session to refresh the connector token.

Related: [[outstanding-items-2026-08-28]] · [[monitorclt]]

## Actual diagnosis (2026-08-28)

Checked from a fresh session with a working Google Calendar connector. A
calendar named **Charlotte REIA** already exists
(`fpmemek77uhmtjn294d9579gsrspbnu0@import.calendar.google.com`) but:

- `accessRole` is **`reader`** — it is a subscribed webcal feed, so nothing
  can write to it, by any client, ever
- it returns **zero events** — the feed itself is empty or broken

**A token refresh cannot fix either condition**, so the remedy recorded in
that session would not have worked. The connector was fine; the calendar is
read-only and its upstream feed is empty.

## Second finding: the connector token expires on writes

On a later attempt the same session could **read** calendars and events fine
but every **write** returned `requires re-authorization (token expired)`. So
there are two independent faults, and the 08-26 session saw only the second:

1. The REIA calendar is a read-only subscription to an empty feed. No token
   refresh can fix this — nothing can write to a subscribed calendar.
2. The Google Calendar connector's write token expires. Reads keep working
   after it does, which makes the connector look healthy.

Re-authorizing fixes (2) but not (1).

## Fix

Two parts:

- **Re-authorize the Google Calendar connector** (claude.ai → Settings →
  Connectors). Writes fail until this is done.
- **Do not try to write to the subscribed REIA calendar.** The connector
  exposes no create-calendar call, so the events belong on the primary
  calendar, titled `Charlotte REIA — <group>`.

## What is actually missing (checked 2026-08-28)

The weekly Wednesday breakfast is **already** on the primary calendar as a
recurring event (`51t8al2oqs05df9ect6an3iuhf`, Wed 8:30 AM) — do not duplicate
it. Only the four monthly subgroups are missing. As monthly recurrences:

| Group | Rule | Time | Venue |
|---|---|---|---|
| B.I.G. Beginner Investors | `FREQ=MONTHLY;BYDAY=1MO` | 6:30 PM | IHOP, 8146 S Tryon **(disputed)** |
| Lancaster, Fort Mill & Rock Hill | `FREQ=MONTHLY;BYDAY=2MO` | 6:30–8:30 PM | The Wine Shop at Rivergate |
| Multi-Family / Commercial | `FREQ=MONTHLY;BYDAY=3MO` | 6:00–8:30 PM | The Wine Shop at Rivergate |
| Concord / Lake Norman / Kannapolis | `FREQ=MONTHLY;BYDAY=3TU` | 6:00–8:00 PM | Panera Bread, 8034 Concord Mills Blvd |

Each rule was checked against the site's published dates for Sep–Nov 2026 and
matches every one.

**Conflict:** charlottereia.com lists B.I.G. at IHOP, but a Meetup-sourced
event on the primary calendar from July 2026 says "NEW LOCATION - Hawthorne's
Pizza, 6215 Old Post Rd". The site may be stale. Verify before attending.
**Conflict:** the site lists the Concord group at 6:00–8:00 PM; existing
calendar entries have 6:30–8:30 PM.
