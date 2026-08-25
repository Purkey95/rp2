---
type: answer
created: 2026-08-25
updated: 2026-08-25
tags: [charlotte, media, rss, monitorclt, pr]
---

# Do the six Charlotte outlets have usable news feeds?

**Yes for two of them natively; yes for the rest through a proxy.** All eight
feeds below were fetched and verified from a datacenter IP on 2026-08-25 and
returned live items. Machine-readable config and a verifier live in
`second-brain/tools/charlotte-news-feeds/`.

This matters for two pillars of [[marketing-strategy-monitorclt]]: Pillar 2
(pitch into a story while it is running) and the repair of the dead GDELT news
monitor.

## Native feeds — the outlet's own RSS

| Outlet | Feed | Items |
|---|---|---|
| WFAE 90.7 | `https://www.wfae.org/news.rss` | 12 |
| WFAE 90.7 (business/development) | `https://www.wfae.org/business.rss` | 10 |
| QCity Metro | `https://qcitymetro.com/rss.xml` | 20 |

**Trap:** WFAE's advertised root feed, `https://www.wfae.org/index.rss`, is the
one linked from its homepage `<head>` — and it parses cleanly while returning
**zero items**. A naive health check sees HTTP 200 and valid XML and reports
success forever. Use the section feeds. QCity Metro is at `/rss.xml`; `/feed/`
and `/rss` both 404.

## Blocked outlets — and what works instead

Four outlets refuse server-side fetches outright:

- **Charlotte Observer** (McClatchy) — connection reset mid-stream (curl err 92)
- **Axios Charlotte** — Cloudflare 403 on `axios.com/local/charlotte/feed` and
  every `api.axios.com/feed/*` path
- **Charlotte Business Journal** — `feeds.bizjournals.com/bizj_charlotte` returns
  S3 AccessDenied; `bizjournals.com/charlotte/news/rss.xml` returns Cloudflare 403
- **The Charlotte Ledger** — publishes at thecharlotteledger.com (Substack custom
  domain) with RSS disabled; every feed path 404s on both domains

**The workaround that works: Google News RSS with a `site:` filter.** No key, no
blocking, real headlines, ~100 items each. Verified returning genuine per-outlet
coverage — "Ballantyne grows with another tower, more than 1,250 new homes"
(Axios), "Contractor's lawsuit seeks $1.2M from Birkdale Place developers" (CBJ),
"Charlotte renters need nearly 4 times minimum wage" (Observer).

The pattern:

```
https://news.google.com/rss/search?q=site:DOMAIN+KEYWORDS&hl=en-US&gl=US&ceid=US:en
```

**The single most useful feed** is the cross-outlet topic sweep, because it shows
what is running in the cycle *right now* across every outlet, including ones not
on this list:

```
https://news.google.com/rss/search?q=Charlotte+NC+(foreclosure+OR+housing+OR+rezoning+OR+%22home+prices%22)+when:7d&hl=en-US&gl=US&ceid=US:en
```

On the verification run it surfaced "Foreclosure of off-campus student housing The
Union and The Mill raises concerns over communication, condition" — exactly the
kind of story where a same-day MonitorCLT number turns a pitch into a citation.

## Limits worth knowing before wiring these in

- Google News gives **headline, outlet, and timestamp** — not article bodies.
  That is enough to know what is running and to pitch, which is the actual job.
  It is not enough for full-text sentiment scoring the way GDELT was doing it.
- Item links are `news.google.com` redirects, not canonical article URLs.
- CBJ is paywalled, so headlines were always the usable signal there.
- For the Ledger, subscribing `hello@cltbuys.com` to the newsletter and parsing
  inbound mail beats any feed — it is newsletter-first and thinly indexed.
- Feeds rot. Treat a feed dropping to zero items as an outage, the same way
  MonitorCLT should treat a pipeline at 0% success. `verify_feeds.py` exits
  non-zero when any feed fails, so it can run on a schedule.

Related: [[marketing-strategy-monitorclt]] · [[monitorclt]]
