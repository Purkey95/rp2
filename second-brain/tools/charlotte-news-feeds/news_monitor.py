#!/usr/bin/env python3
"""Charlotte housing news monitor - a replacement for MonitorCLT's dead GDELT job.

Reads the verified feeds in feeds.json, stores what it finds in SQLite, scores
each story for Charlotte housing relevance, and matches the ones worth pitching
to the MonitorCLT data asset that answers them.

The point is not to read the news. It is to know, within hours, which housing
story is running so a MonitorCLT number can be offered to the reporter while the
story is still live. That timing is the whole of Pillar 2.

    python3 news_monitor.py --once            # fetch, store, print new stories
    python3 news_monitor.py --digest          # re-print the last 24h from the DB
    python3 news_monitor.py --digest --since 7d
    python3 news_monitor.py --once --email    # MonitorCLT-format alert body
    python3 news_monitor.py --once --json     # machine-readable, for a metrics sink

Stdlib only - no pip install, no feedparser. Runs anywhere Python 3.9+ does.

Exit codes:  0 = ran clean   1 = one or more feeds failed (treat as an outage)
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

FEEDS_FILE = Path(__file__).with_name("feeds.json")
DB_PATH = os.environ.get("CLT_NEWS_DB", str(Path(__file__).with_name("clt_news.db")))
TIMEOUT = int(os.environ.get("CLT_NEWS_TIMEOUT", "25"))
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

# ── Relevance scoring ────────────────────────────────────────────────────────
# Weights are deliberately blunt. A story only needs to clear the threshold to
# get a human's attention; precision past that buys nothing.

TOPIC_WEIGHTS = {
    "foreclosure":    (["foreclosure", "foreclosed", "auction", "distressed", "trustee sale"], 5),
    "lending":        (["mortgage", "hard money", "private lender", "interest rate",
                        "rate cut", "lending", "loan"], 4),
    "prices":         (["home price", "median price", "housing market", "inventory",
                        "affordability", "rent", "renters"], 4),
    "development":    (["rezoning", "rezone", "development", "apartments", "mixed-use",
                        "groundbreaking", "tower", "subdivision", "permit"], 3),
    "investors":      (["investor", "llc", "out-of-state", "institutional", "hedge fund",
                        "corporate landlord", "build-to-rent"], 4),
    "distress":       (["eviction", "layoff", "bankruptcy", "code enforcement",
                        "condemned", "blight", "tax lien"], 4),
    "policy":         (["city council", "county commission", "zoning", "housing trust",
                        "affordable housing", "lihtc", "hud"], 3),
}

# Geography gate. A story about the national housing market is not a Charlotte
# pitch, and the topic feeds pull in plenty of those.
GEO_TERMS = ["charlotte", "mecklenburg", "clt", "queen city", "huntersville",
             "matthews", "concord", "gastonia", "ballantyne", "north carolina",
             "cornelius", "davidson", "pineville", "mint hill", "belmont"]

# ── Pitch matching ───────────────────────────────────────────────────────────
# Each topic maps to the MonitorCLT asset that answers it, so the digest tells
# you what to send rather than leaving you to work it out at pitch time.

PITCH = {
    "foreclosure": "Foreclosure Auction Report - 51 completed auctions, 70.6% won by "
                   "third parties, median clearing price $219k",
    "lending":     "Private Lending Leaderboard - 986 private/hard-money loans in 30d, "
                   "median $208k, ranked by lender",
    "prices":      "'The 72,405' - Meck owners holding loans 2+ pts below today's 6.67%, "
                   "the supply-suppression number",
    "investors":   "Buyer Mix report - 30.1% of detected sales to entities/LLCs, 13.2% "
                   "out-of-state, both falling MoM",
    "development": "Rezoning + development tracker - 86 petitions, 3,825 funded road "
                   "projects, 83 transit stations",
    "distress":    "WARN layoffs + code enforcement overlay against foreclosure filings",
    "policy":      "Programs catalog - 71 active federal/state/local programs, "
                   "15 on recurring feeds",
}

# A local story carrying one strong topic should clear the bar on its own: a
# headline like "Charlotte-area foreclosures spike 71%" is the single best pitch
# opportunity there is, and it only matches one topic. The bonus is tuned so
# foreclosure/lending/prices/investors/distress stories become pitches while
# generic development and policy coverage stays as background.
LOCAL_BONUS = 3
PITCH_THRESHOLD = 7   # score at or above this is worth a same-day pitch

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url_hash    TEXT PRIMARY KEY,
    feed_id     TEXT NOT NULL,
    outlet      TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT,
    published   TEXT,
    fetched_at  TEXT NOT NULL,
    score       INTEGER DEFAULT 0,
    topics      TEXT DEFAULT '',
    is_local    INTEGER DEFAULT 0,
    notified    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_score   ON articles(score DESC);

CREATE TABLE IF NOT EXISTS feed_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id   TEXT NOT NULL,
    ran_at    TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    items     INTEGER DEFAULT 0,
    error     TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def clean_title(title, outlet):
    """Google News appends ' - Publisher' to every headline. Strip it."""
    title = re.sub(r"\s+", " ", title).strip()
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", title) if "news.google" in outlet else title


def matches(term, text):
    """Word-boundary match, tolerant of simple plurals.

    Plain substring matching double-counts: "rezoning" contains "zoning", so a
    routine rezoning filing scored as both development AND policy and outranked
    a live foreclosure story.

    But bare boundaries are too strict in the other direction - `\\bforeclosure\\b`
    does not match "foreclosures", and headlines are overwhelmingly plural
    ("Charlotte-area foreclosures spike 71%"). Allow a trailing s/es.
    """
    return re.search(rf"\b{re.escape(term)}(?:e?s)?\b", text) is not None


def score(title):
    """Return (score, sorted topics, is_local). Title-only: bodies aren't in the feeds."""
    low = title.lower()
    total, topics = 0, []
    for topic, (terms, weight) in TOPIC_WEIGHTS.items():
        if any(matches(t, low) for t in terms):
            total += weight
            topics.append(topic)
    is_local = any(matches(g, low) for g in GEO_TERMS)
    if is_local:
        # Only reward locality when the story is actually about housing. Without
        # this guard a Charlotte museum piece scores 3 and pollutes the digest.
        if total:
            total += LOCAL_BONUS
    else:
        # A national housing story is background, not a pitch. Halve it rather
        # than dropping it - occasionally a national piece needs a local number.
        total = total // 2
    return total, sorted(topics), is_local


def parse_feed(body):
    """Yield (title, link, published) from RSS or Atom. Returns [] on unparseable XML."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []

    out = []
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            out.append((title, link, pub))
    if out:
        return out

    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{ns}entry"):
        title = (entry.findtext(f"{ns}title") or "").strip()
        link_el = entry.find(f"{ns}link")
        link = link_el.get("href", "") if link_el is not None else ""
        pub = (entry.findtext(f"{ns}updated") or entry.findtext(f"{ns}published") or "").strip()
        if title:
            out.append((title, link, pub))
    return out


def normalize_date(raw):
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return raw[:32]


def fetch(feed):
    req = urllib.request.Request(feed["url"], headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def run_once(conn, verbose=True):
    """Fetch every feed, store new articles, return (new_articles, failures)."""
    feeds = json.loads(FEEDS_FILE.read_text())["feeds"]
    new, failures = [], []

    for feed in feeds:
        try:
            body = fetch(feed)
            entries = parse_feed(body)
            if not entries:
                raise ValueError("parsed but empty - 0 items")
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            failures.append((feed["id"], str(exc)[:120]))
            conn.execute(
                "INSERT INTO feed_runs (feed_id, ran_at, ok, items, error) VALUES (?,?,0,0,?)",
                (feed["id"], now_iso(), str(exc)[:200]))
            if verbose:
                print(f"  [FAIL] {feed['outlet']}: {str(exc)[:90]}", file=sys.stderr)
            continue

        conn.execute("INSERT INTO feed_runs (feed_id, ran_at, ok, items) VALUES (?,?,1,?)",
                     (feed["id"], now_iso(), len(entries)))

        for title, link, pub in entries:
            title = clean_title(title, feed["url"])
            if not title or title.lower() == "google news":
                continue
            # Stable digest, not hash(): Python salts hash() per process, so a
            # built-in hash would change every run and dedupe would silently fail.
            url_hash = hashlib.sha1((link or title).encode("utf-8")).hexdigest()
            pts, topics, is_local = score(title)
            cur = conn.execute(
                """INSERT OR IGNORE INTO articles
                   (url_hash, feed_id, outlet, title, url, published, fetched_at,
                    score, topics, is_local)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (url_hash, feed["id"], feed["outlet"], title, link,
                 normalize_date(pub), now_iso(), pts, ",".join(topics), int(is_local)))
            if cur.rowcount:
                new.append({"outlet": feed["outlet"], "title": title, "url": link,
                            "score": pts, "topics": topics, "is_local": is_local})

    conn.commit()
    return new, failures


def parse_since(text):
    match = re.fullmatch(r"(\d+)([hd])", text.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError("use forms like 24h or 7d")
    count, unit = int(match.group(1)), match.group(2)
    return timedelta(hours=count) if unit == "h" else timedelta(days=count)


def load_recent(conn, since):
    """Recent by PUBLICATION date, not fetch date.

    Google News `site:` queries happily return years-old articles. Filtering on
    fetched_at means the first run treats a 2022 piece as breaking news - which
    is exactly how you embarrass yourself pitching a reporter. Fall back to
    fetched_at only when the feed supplied no usable date.
    """
    cutoff = (datetime.now(timezone.utc) - since).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT * FROM articles
           WHERE (published != '' AND published >= ?)
              OR (published = '' AND fetched_at >= ?)
           ORDER BY score DESC, published DESC""",
        (cutoff, cutoff)).fetchall()
    return [dict(r) for r in rows]


def render(rows, failures, since_label, email=False):
    """Render a digest. `email` uses the MonitorCLT alert voice and drops the tips."""
    pitchable = [r for r in rows if r["score"] >= PITCH_THRESHOLD and r["is_local"]]
    # score > 0 keeps non-housing local news (arts, sport, crime) out of the digest.
    local = [r for r in rows if r["is_local"] and r["score"] > 0 and r not in pitchable]
    lines = []

    head = f"Charlotte housing news - {len(rows)} stories in the last {since_label}"
    lines.append(("📰 " if email else "") + head)
    lines.append("=" * min(len(head) + 2, 78))
    lines.append("")

    if pitchable:
        lines.append(f"🔥 PITCH NOW - {len(pitchable)} live " +
                     ("story" if len(pitchable) == 1 else "stories") +
                     " a MonitorCLT number answers")
        lines.append("")
        for row in pitchable:
            topics = row["topics"].split(",") if isinstance(row["topics"], str) else row["topics"]
            lines.append(f"  [{row['score']:>2}] {row['title']}")
            lines.append(f"       {row['outlet']}")
            for topic in topics:
                if topic in PITCH:
                    lines.append(f"       → send: {PITCH[topic]}")
            if row.get("url"):
                lines.append(f"       {row['url'][:100]}")
            lines.append("")
    else:
        lines.append("No pitchable Charlotte housing story in this window.")
        lines.append("")

    if local:
        lines.append(f"📍 Other local coverage ({len(local)})")
        for row in local[:12]:
            lines.append(f"  [{row['score']:>2}] {row['title'][:88]}  · {row['outlet']}")
        lines.append("")

    if failures:
        lines.append(f"⚠️  {len(failures)} feed(s) FAILED - treat as an outage:")
        for feed_id, err in failures:
            lines.append(f"  • {feed_id}: {err[:80]}")
        lines.append("")

    if not email:
        lines.append("-" * 78)
        lines.append("Pitch within hours, not days. A reporter on deadline takes the number")
        lines.append("that arrives while the story is open and nothing after it.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="fetch all feeds, then report")
    mode.add_argument("--digest", action="store_true", help="report from the DB without fetching")
    parser.add_argument("--since", type=parse_since, default="24h",
                        help="window for the digest, e.g. 24h or 7d (default 24h)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--email", action="store_true", help="MonitorCLT-format alert body")
    parser.add_argument("--new-only", action="store_true",
                        help="with --once, report only stories first seen this run")
    args = parser.parse_args()

    if not (args.once or args.digest):
        args.once = True
    since = args.since if isinstance(args.since, timedelta) else parse_since(args.since)
    since_label = f"{int(since.total_seconds() // 3600)}h"

    conn = connect()
    failures = []
    if args.once:
        new, failures = run_once(conn, verbose=not args.json)
        rows = new if args.new_only else load_recent(conn, since)
        if args.new_only:
            for row in rows:
                row.setdefault("topics", [])
                row["topics"] = ",".join(row["topics"]) if isinstance(row["topics"], list) else row["topics"]
    else:
        rows = load_recent(conn, since)

    if args.json:
        print(json.dumps({
            "generated": now_iso(),
            "window": since_label,
            "total": len(rows),
            "pitchable": [r for r in rows if r["score"] >= PITCH_THRESHOLD and r["is_local"]],
            "stories": rows,
            "failed_feeds": [{"feed_id": f, "error": e} for f, e in failures],
        }, indent=2, default=str))
    else:
        print(render(rows, failures, since_label, email=args.email))

    conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
