#!/usr/bin/env python3
"""Regression tests for the news_monitor relevance scorer.

Every case here is a bug that actually shipped during development. The keyword
lists are the kind of thing that gets edited casually months later, so pin the
behaviour: run this before changing TOPIC_WEIGHTS, GEO_TERMS, or LOCAL_BONUS.

    python3 test_scoring.py     # exit 0 = all pass
"""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("nm", Path(__file__).with_name("news_monitor.py"))
nm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nm)

# (headline, expect_pitchable, expect_topics_contain, note)
CASES = [
    ("Charlotte-area foreclosures spike 71%. What's pushing homeowners to the brink?",
     True, "foreclosure",
     "plural must match singular term - bare \\b boundaries missed 'foreclosures'"),

    ("American Asset Corp. seeks rezoning for southwest Charlotte site",
     False, "development",
     "'rezoning' must not also score 'zoning'/policy - substring double-count"),

    ("Charlotte museum's newest painting honors Black history",
     False, None,
     "non-housing local news must score 0, not collect the locality bonus"),

    ("National home prices climb again in July",
     False, "prices",
     "non-local housing story is background, halved, never a pitch"),

    ("Mecklenburg County eviction filings jump 40%",
     True, "distress",
     "one strong topic plus locality clears the bar on its own"),

    ("Investors bought a record share of Charlotte homes last quarter",
     True, "investors",
     "the buyer-mix pitch trigger"),

    ("Charlotte hard money lending volume hits new high",
     True, "lending",
     "the lending-leaderboard pitch trigger"),
]


def main():
    failures = []
    for headline, want_pitch, want_topic, note in CASES:
        total, topics, is_local = nm.score(headline)
        pitchable = total >= nm.PITCH_THRESHOLD and is_local

        if pitchable != want_pitch:
            failures.append(
                f"pitchable={pitchable} want={want_pitch} (score {total}) :: {headline}\n"
                f"      why it matters: {note}")
        elif want_topic and want_topic not in topics:
            failures.append(
                f"topics={topics} missing {want_topic!r} :: {headline}\n"
                f"      why it matters: {note}")
        else:
            print(f"  pass  score={total:>2} {'PITCH' if pitchable else '     '}  {headline[:62]}")

    if failures:
        print(f"\n{len(failures)} FAILED:\n", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"\n{len(CASES)}/{len(CASES)} scoring cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
