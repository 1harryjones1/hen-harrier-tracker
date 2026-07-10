"""
Tracks per-source fetch health in data/processed/fetch_status.json, feeding
the site's "last successfully refreshed" transparency footer (doc §9).

On failure, last_success_at is left untouched - the principle from the doc
is to keep the last-known-good processed data visible rather than blanking
the site, while still surfacing that a fetch attempt failed.
"""

import json
import os

from . import raw_snapshot

_STATUS_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "fetch_status.json")


def record_fetch_result(source_name, ok, detail=None):
    """Record the outcome of a fetch attempt for `source_name`."""
    os.makedirs(os.path.dirname(_STATUS_PATH), exist_ok=True)
    data = {}
    if os.path.exists(_STATUS_PATH):
        with open(_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

    entry = data.get(source_name, {})
    now = raw_snapshot.utcnow_iso()
    entry["last_attempt_at"] = now
    entry["ok"] = ok
    if ok:
        entry["last_success_at"] = now
    entry["last_detail"] = detail
    data[source_name] = entry

    with open(_STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def load_fetch_status():
    if not os.path.exists(_STATUS_PATH):
        return {}
    with open(_STATUS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
