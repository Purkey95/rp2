# Charlotte news monitor

A replacement for MonitorCLT's dead GDELT News Monitor, scoped to what actually
earns money: knowing which Charlotte housing story is running *right now* so a
MonitorCLT number can reach the reporter while the story is still open.

Stdlib only. No pip install, no feedparser, Python 3.9+.

## Files

| File | Purpose |
|---|---|
| `feeds.json` | The 8 verified feeds, with a note on each explaining why it's native or proxied |
| `verify_feeds.py` | Health check: fetches each feed, counts items, exits non-zero on failure |
| `news_monitor.py` | Fetch → score → store → digest |
| `test_scoring.py` | Regression tests for the scorer; run before editing keywords |
| `clt_news.db` | SQLite store (gitignored, created on first run) |

## Use

```bash
python3 news_monitor.py --once              # fetch, store, print the digest
python3 news_monitor.py --digest --since 7d # re-print from the DB, no fetching
python3 news_monitor.py --once --email      # MonitorCLT-format alert body
python3 news_monitor.py --once --json       # for a metrics sink
python3 news_monitor.py --once --email --only-if-actionable   # silent when quiet
python3 verify_feeds.py --quiet             # feed health only
python3 test_scoring.py                     # scorer regression tests
```

Environment: `CLT_NEWS_DB` sets the SQLite path (default: alongside the script),
`CLT_NEWS_TIMEOUT` the per-feed fetch timeout in seconds.

## Wiring it into MonitorCLT

Three integration points, in the order they're worth doing:

1. **Schedule it.** Twice daily beats hourly — you pitch on a story, you don't
   day-trade it. Use `--only-if-actionable` so it mails you *only* when there's a
   pitchable story or a failed feed; a digest that arrives every day regardless
   is a digest that stops being read, which is how four MonitorCLT pipelines sat
   dead for eleven days.

   ```cron
   0 7,15 * * * cd /path/to/charlotte-news-feeds && \
     out=$(python3 news_monitor.py --once --email --only-if-actionable) && \
     [ -n "$out" ] && printf '%s' "$out" | \
     mail -s "[MonitorCLT alert] Charlotte housing news" you@cltbuys.com
   ```
2. **Register it as a pipeline** so it appears in the metrics digest alongside
   the others. It already exits non-zero when any feed fails, which is the
   signal `pipeline.success_rate_7d` needs.
3. **Point it at the shared DB.** Set `CLT_NEWS_DB` to MonitorCLT's SQLite file.
   The two tables (`articles`, `feed_runs`) are prefixed distinctly and use
   `CREATE TABLE IF NOT EXISTS`, so they won't collide with existing schema.

## How scoring works

Each headline is matched against seven weighted topic groups (foreclosure,
lending, prices, development, investors, distress, policy). A story that matches
any topic *and* mentions somewhere in the Charlotte metro gets `LOCAL_BONUS`;
a housing story with no local mention is halved to background. At or above
`PITCH_THRESHOLD` the digest promotes it to **PITCH NOW** and names the
MonitorCLT asset that answers it.

The thresholds are tuned so a single strong local signal — "Charlotte-area
foreclosures spike 71%" — is a pitch on its own, while routine rezoning and
policy coverage stays as background. Retune by editing the constants at the top
of `news_monitor.py`, then re-run `test_scoring.py`.

## Known limits

- **Titles only.** Feeds don't carry article bodies, so a headline that buries
  the housing angle is missed, and an off-topic headline that happens to use the
  word "investor" (a VC funding story, say) can score as a false positive. Expect
  roughly one or two per digest; that's the cost of a zero-dependency monitor.
- **Google News gives headline, outlet, and timestamp — not article text.** Enough
  to know what's running and to pitch. Not enough for the full-text sentiment
  scoring GDELT was doing.
- **Item links are `news.google.com` redirects**, not canonical article URLs.
- **The topic sweep pulls in YouTube and aggregators**, not just the six outlets.
- **Feeds rot.** Run `verify_feeds.py` on a schedule and treat zero items as an
  outage, not silence. WFAE's own advertised root feed already fails this way:
  valid XML, HTTP 200, zero items, forever.
