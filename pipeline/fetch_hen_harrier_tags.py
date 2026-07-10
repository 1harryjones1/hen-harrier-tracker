#!/usr/bin/env python
"""
Stage A fetcher for the NE hen harrier tracking update.

Scrapes the gov.uk page for the newest dated update round, downloads its
.ods spreadsheet, and writes an immutable raw snapshot plus a small metadata
sidecar recording which round/URL it came from (so parse_hen_harrier_tags.py
can be re-run against an already-downloaded file without re-hitting gov.uk).
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.fetch_status import record_fetch_result
from lib.govuk_scraper import USER_AGENT, ScraperError, find_latest_update

SOURCE_DIR = "ne_hen_harrier_ods"
SOURCE_NAME = "ne_hen_harrier_tags"


def fetch_update(update):
    """Download one update round's .ods file and write raw snapshot + metadata sidecar. Returns the metadata dict."""
    resp = requests.get(update["ods_url"], headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()

    timestamp = raw_snapshot.utcnow_compact()
    ods_path, written = raw_snapshot.write_raw(SOURCE_DIR, f"{timestamp}.ods", resp.content, mode="wb")

    meta = {
        "label": update["label"],
        "ods_url": update["ods_url"],
        "page_url": update["page_url"],
        "fetched_at": raw_snapshot.utcnow_iso(),
        "raw_path": raw_snapshot.relative_path(ods_path),
    }
    meta_path, _ = raw_snapshot.write_raw(SOURCE_DIR, f"{timestamp}.meta.json", json.dumps(meta, indent=2))
    return meta


def main():
    try:
        update = find_latest_update()
        meta = fetch_update(update)
        record_fetch_result(SOURCE_NAME, ok=True, detail=f"{update['label']} -> {meta['raw_path']}")
        print(f"Fetched {update['label']!r}: {meta['raw_path']}")
        return 0
    except (requests.RequestException, ScraperError) as exc:
        record_fetch_result(SOURCE_NAME, ok=False, detail=str(exc))
        print(f"FAILED to fetch NE hen harrier table: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
