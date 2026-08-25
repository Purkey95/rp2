#!/usr/bin/env python3
"""Verify the Charlotte news feeds in feeds.json still resolve and carry items.

Feeds rot: sites redesign, Cloudflare rules tighten, section slugs move. Run this
on a schedule and treat a drop to zero items the same way MonitorCLT treats a
pipeline at 0% success - as an outage, not a quiet nothing.

    python3 verify_feeds.py            # check all, human-readable
    python3 verify_feeds.py --json     # machine-readable, for a metrics sink
    python3 verify_feeds.py --quiet    # print only failures; exit 1 if any
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

FEEDS_FILE = Path(__file__).with_name("feeds.json")
TIMEOUT = 25
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

ITEM_RE = re.compile(rb"<(?:item|entry)[\s>]", re.IGNORECASE)
TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def check(feed):
    """Fetch one feed and report item count and first headline."""
    result = {"id": feed["id"], "outlet": feed["outlet"], "url": feed["url"]}
    req = urllib.request.Request(feed["url"], headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            result["http"] = resp.status
    except urllib.error.HTTPError as exc:
        result.update(http=exc.code, items=0, ok=False, error=f"HTTP {exc.code}")
        return result
    except Exception as exc:  # noqa: BLE001 - any transport failure is a feed failure
        result.update(http=0, items=0, ok=False, error=str(exc)[:120])
        return result

    items = len(ITEM_RE.findall(body))

    # Take the first title that appears *inside* an item, not the channel-level
    # ones. Google News feeds carry two channel titles before the first entry, so
    # indexing blindly into the title list reports "Google News" as the headline.
    sample = ""
    first_item = ITEM_RE.search(body)
    if first_item:
        match = TITLE_RE.search(body, first_item.start())
        if match:
            raw = re.sub(rb"<!\[CDATA\[|\]\]>", b"", match.group(1))
            sample = raw.decode("utf-8", "replace").strip()

    result.update(items=items, sample=sample[:110], ok=items > 0)
    if items == 0:
        result["error"] = "parsed but empty - feed exists and returns 0 items"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args()

    feeds = json.loads(FEEDS_FILE.read_text())["feeds"]
    results = [check(f) for f in feeds]
    failures = [r for r in results if not r["ok"]]

    if args.json:
        print(json.dumps({"results": results, "failed": len(failures)}, indent=2))
    else:
        for r in results:
            if args.quiet and r["ok"]:
                continue
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"[{mark}] {r['items']:>3} items  {r['outlet']}")
            if r["ok"]:
                print(f"          {r['sample']}")
            else:
                print(f"          {r.get('error', 'unknown')}  <- {r['url'][:88]}")
        print(f"\n{len(results) - len(failures)}/{len(results)} feeds healthy")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
