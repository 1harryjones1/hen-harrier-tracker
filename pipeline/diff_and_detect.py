#!/usr/bin/env python
"""
Stage C: diff & detect status changes in the hen harrier tag-status table.

Loads the currently-committed birds.json as "previous", parses the latest
fetched raw snapshot as "latest", and appends any detected status changes /
newly-tagged birds to the append-only bird_events.json log - never editing
history, only adding to it. birds.json itself is only overwritten with
"latest" after the diff against the old state has been computed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from parse_hen_harrier_tags import (
    BIRDS_JSON_PATH,
    find_latest_raw_snapshot,
    parse_snapshot,
    write_birds_json,
)

EVENTS_JSON_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "bird_events.json")
SCHEMA_VERSION = 1


def load_previous_birds():
    if not os.path.exists(BIRDS_JSON_PATH):
        return None
    with open(BIRDS_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_events():
    if not os.path.exists(EVENTS_JSON_PATH):
        return {"schema_version": SCHEMA_VERSION, "events": []}
    with open(EVENTS_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_events(data):
    os.makedirs(os.path.dirname(EVENTS_JSON_PATH), exist_ok=True)
    with open(EVENTS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def _make_event(event_type, bird_id, bird, latest_source, extra=None):
    event = {
        "event_id": f"evt_{raw_snapshot.utcnow_compact()}_{bird_id}",
        "bird_id": bird_id,
        "bird_name": bird.get("name", ""),
        "event_type": event_type,
        "detected_at": raw_snapshot.utcnow_iso(),
        "source_update_label": latest_source["update_label"],
        "source_url": latest_source["source_url"],
        "raw_snapshot_path": latest_source["raw_snapshot_path"],
        "location_status": bird.get("location_status"),
        "coordinates": bird.get("coordinates"),
    }
    if extra:
        event.update(extra)
    return event


def detect_events(previous_birds, latest_birds, latest_source):
    """Compare `previous_birds` (dict keyed by bird_id, or None on first run) against `latest_birds`."""
    events = []
    previous_birds = previous_birds or {}

    for bird_id, bird in latest_birds.items():
        prev = previous_birds.get(bird_id)
        if prev is None:
            if previous_birds:
                # A real prior snapshot exists, so this bird genuinely is new.
                # (On the very first-ever run, every bird is trivially "new" -
                # that's just the baseline, not news, so we skip emitting
                # events in that case - see main()'s "first run" branch.)
                events.append(_make_event("new_bird_tagged", bird_id, bird, latest_source))
            continue
        if bird.get("status") != prev.get("status"):
            events.append(_make_event(
                "status_change", bird_id, bird, latest_source,
                extra={"previous_status": prev.get("status"), "new_status": bird.get("status")},
            ))
        elif bird.get("status") == "alive" and bird.get("location_text") != prev.get("location_text"):
            events.append(_make_event(
                "location_updated", bird_id, bird, latest_source,
                extra={
                    "previous_location_text": prev.get("location_text"),
                    "new_location_text": bird.get("location_text"),
                },
            ))

    if previous_birds:
        vanished = set(previous_birds) - set(latest_birds)
        for bird_id in sorted(vanished):
            print(
                f"ANOMALY: bird_id {bird_id!r} was in the previous snapshot but is missing from the latest "
                "one - NE's table has historically been cumulative, so a disappearing row is unexpected. "
                "Not raised as an event; check the source manually.",
                file=sys.stderr,
            )

    return events


def main():
    previous_data = load_previous_birds()
    previous_birds = previous_data["birds"] if previous_data else None

    latest_meta = find_latest_raw_snapshot()
    if latest_meta is None:
        print("No raw hen harrier snapshots found - run fetch_hen_harrier_tags.py first.", file=sys.stderr)
        return 1
    latest_data = parse_snapshot(latest_meta)

    if previous_data and previous_data["source"]["update_label"] == latest_data["source"]["update_label"]:
        print(f"Already up to date with {latest_data['source']['update_label']!r} - nothing to diff.")
        return 0

    new_events = detect_events(previous_birds, latest_data["birds"], latest_data["source"])

    events_data = load_events()
    events_data["events"].extend(new_events)
    write_events(events_data)

    write_birds_json(latest_data)

    if previous_birds is None:
        print(f"First run: established baseline of {len(latest_data['birds'])} birds, 0 events (not a bug).")
    else:
        print(
            f"Diffed {previous_data['source']['update_label']!r} -> {latest_data['source']['update_label']!r}: "
            f"{len(new_events)} new event(s)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
