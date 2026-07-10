#!/usr/bin/env python
"""
Fetches the Raptor Persecution UK RSS feed, writes an immutable raw
snapshot, and records any harrier-relevant entries as candidate
"unconfirmed reports" (doc §10 Phase 1's separate feed). RPUK is often the
first, and for Scotland sometimes the *only*, public record of an incident
(doc §3) - but it's independent, fast-moving reporting with a real
false-positive risk for automated extraction, so this stays firmly in the
"unconfirmed" tier (see lib/report_matcher.py).
"""

import os
import sys

import feedparser
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.fetch_status import record_fetch_result
from lib.report_matcher import is_harrier_relevant, load_known_bird_names, match_bird_names, parsed_time_to_iso, upsert_reports

FEED_URL = "https://raptorpersecutionuk.org/feed"
USER_AGENT = "hen-harrier-tracker/0.1 (public-interest raptor-persecution tracking pipeline)"
SOURCE_DIR = "rpuk_rss"
SOURCE_NAME = "rpuk_rss"
SOURCE_LABEL = "Raptor Persecution UK"


def main():
    try:
        resp = requests.get(FEED_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        record_fetch_result(SOURCE_NAME, ok=False, detail=str(exc))
        print(f"FAILED to fetch RPUK feed: {exc}", file=sys.stderr)
        return 1

    raw_snapshot.write_raw(SOURCE_DIR, f"{raw_snapshot.utcnow_compact()}.xml", resp.text)

    feed = feedparser.parse(resp.content)
    known_names = load_known_bird_names()
    fetched_at = raw_snapshot.utcnow_iso()

    new_reports = []
    for entry in feed.entries:
        text = f"{entry.get('title', '')} {entry.get('summary', '')}"
        if not is_harrier_relevant(text):
            continue
        report_id = f"rpuk:{entry.get('id') or entry.get('link')}"
        new_reports.append({
            "report_id": report_id,
            "source_name": SOURCE_LABEL,
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published_at": parsed_time_to_iso(entry.get("published_parsed")),
            "fetched_at": fetched_at,
            "matched_bird_ids": match_bird_names(text, known_names),
            "matched_keywords": ["harrier"],
        })

    added = upsert_reports(new_reports)
    record_fetch_result(SOURCE_NAME, ok=True, detail=f"{len(feed.entries)} entries, {added} new harrier-relevant")
    print(f"RPUK: {len(feed.entries)} entries checked, {added} new harrier-relevant report(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
