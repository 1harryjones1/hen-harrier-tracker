#!/usr/bin/env python
"""
Stage B parser for the NE hen harrier tracking update.

Finds the most recently-published raw .ods snapshot (by update-round label,
e.g. "June 2026 update" - not fetch order, so backfilling an older
historical round after already fetching a newer one doesn't confuse
"latest"), parses it into the birds.json schema, and writes
data/processed/birds.json.

diff_and_detect.py is the normal production entrypoint: it loads the
currently-committed birds.json as "previous" *before* calling parse_snapshot()
here to compute "latest", then overwrites birds.json itself once the diff is
done. Running this script's main() directly is only for the first-ever
bootstrap (no prior birds.json to preserve) or standalone parser debugging -
it will happily clobber any existing birds.json without diffing it.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.govuk_scraper import label_sort_key
from lib.ods_parser import parse_birds_from_ods

SOURCE_DIR = "ne_hen_harrier_ods"
SCHEMA_VERSION = 1
BIRDS_JSON_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "birds.json")


def _raw_dir():
    return os.path.join(raw_snapshot.repo_root(), "data", "raw", SOURCE_DIR)


def list_raw_snapshots():
    """Return metadata dicts for every fetched raw snapshot, each augmented with its .ods path under '_ods_path'."""
    metas = []
    for meta_path in sorted(glob.glob(os.path.join(_raw_dir(), "*.meta.json"))):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ods_path = meta_path[: -len(".meta.json")] + ".ods"
        if os.path.exists(ods_path):
            meta["_ods_path"] = ods_path
            metas.append(meta)
    return metas


def find_snapshot_by_label_substring(substring):
    """Find the most recently-fetched raw snapshot whose update label contains `substring` (case-insensitive)."""
    matches = [m for m in list_raw_snapshots() if substring.lower() in m["label"].lower()]
    if not matches:
        return None
    matches.sort(key=lambda m: m["fetched_at"], reverse=True)
    return matches[0]


def find_latest_raw_snapshot():
    """Return the fetched raw snapshot whose update-round label is newest (by label, not fetch/filesystem order)."""
    dated = [(label_sort_key(m["label"]), m) for m in list_raw_snapshots()]
    dated = [d for d in dated if d[0] is not None]
    if not dated:
        return None
    dated.sort(key=lambda d: d[0], reverse=True)
    return dated[0][1]


def _dedupe_bird_ids(records):
    """
    Build a bird_id -> record dict, disambiguating real collisions.

    Observed live in the June 2026 file: NE's own table reuses a plain
    numeric Tag ID for two entirely different birds ("Alex", deceased, and
    "Harriet") without the documented "a" reuse-suffix that's supposed to
    flag this. A naive dict-by-bird_id would silently drop one bird's
    record. Instead, a colliding record is kept under a synthetic
    "<bird_id>#row<row_index>" key, and the collision is logged loudly so
    it can be checked against the source by a human - this is a genuine
    source data-quality issue, not a parser bug to hide.
    """
    birds = {}
    for record in records:
        bird_id = record["bird_id"]
        if bird_id in birds:
            disambiguated = f"{bird_id}#row{record['row_index_in_source']}"
            print(
                f"WARNING: duplicate Tag ID {bird_id!r} in source data "
                f"(rows {birds[bird_id]['row_index_in_source']} and {record['row_index_in_source']}) - "
                f"storing second occurrence as {disambiguated!r}",
                file=sys.stderr,
            )
            record = {**record, "bird_id": disambiguated}
            bird_id = disambiguated
        birds[bird_id] = record
    return birds


def parse_snapshot(meta):
    """Parse one fetched raw snapshot (from list_raw_snapshots/find_*) into the birds.json-shaped dict."""
    records = parse_birds_from_ods(meta["_ods_path"])
    birds = _dedupe_bird_ids(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": raw_snapshot.utcnow_iso(),
        "source": {
            "update_label": meta["label"],
            "source_url": meta["ods_url"],
            "fetched_at": meta["fetched_at"],
            "raw_snapshot_path": meta["raw_path"],
        },
        "birds": birds,
    }


def write_birds_json(data):
    os.makedirs(os.path.dirname(BIRDS_JSON_PATH), exist_ok=True)
    with open(BIRDS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def main():
    meta = find_latest_raw_snapshot()
    if meta is None:
        print("No raw hen harrier snapshots found - run fetch_hen_harrier_tags.py first.", file=sys.stderr)
        return 1
    data = parse_snapshot(meta)
    write_birds_json(data)
    print(f"Parsed {len(data['birds'])} birds from {meta['label']!r} -> {BIRDS_JSON_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
