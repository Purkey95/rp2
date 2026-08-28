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

## Fix

Create a *writable* calendar and populate it from the Charlotte REIA site
directly, rather than subscribing to the feed. Awaiting a go-ahead.
