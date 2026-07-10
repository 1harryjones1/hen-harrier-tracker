#!/usr/bin/env python
"""
Stage H: build the public data consumed by the static site.

Merges the canonical current bird state (data/processed/birds.json) with any
confirmed-located habitat annotations - only where a human has flipped the
corresponding review_queue.json entry (lib/review_state.py) - into
data/published/incidents.geojson. Republishes unconfirmed RSS/blog reports
as citation-only entries (title/source/link/date - never the pipeline's own
inferred bird/location match, see doc §5/§11), and a per-source freshness
summary for the site's transparency footer (doc §9). Finally copies
everything in data/published/ into site/data/, which is the only place the
static site itself reads from.

Re-running this script is enough to reflect a review-status change in the
published output - it never recomputes source facts itself.
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.fetch_status import load_fetch_status
from lib.review_state import STATUS_CONFIRMED_LOCATED, load_review_queue

REPO_ROOT = raw_snapshot.repo_root()
BIRDS_JSON_PATH = os.path.join(REPO_ROOT, "data", "processed", "birds.json")
EVENTS_JSON_PATH = os.path.join(REPO_ROOT, "data", "processed", "bird_events.json")
MATCHES_JSON_PATH = os.path.join(REPO_ROOT, "data", "processed", "moorland_matches.json")
UNCONFIRMED_REPORTS_PATH = os.path.join(REPO_ROOT, "data", "processed", "unconfirmed_reports.json")

PUBLISHED_DIR = os.path.join(REPO_ROOT, "data", "published")
SITE_DATA_DIR = os.path.join(REPO_ROOT, "site", "data")

STATUS_LABELS = {
    "alive": "Alive, transmitting",
    "dead": "Dead",
    "missing_fate_unknown": "Missing, fate unknown",
    "unknown": "Status unknown",
}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def _latest_event_for_bird(events, bird_id):
    """Return the most recently detected_at event for `bird_id`, or None. A bird only oscillates
    status in rare/unusual cases, but this picks the right one if it ever does."""
    bird_events = [e for e in events if e["bird_id"] == bird_id]
    if not bird_events:
        return None
    return max(bird_events, key=lambda e: e["detected_at"])


# Per-layer description formatters. Each layer's raw attributes have a
# different shape and not all are clean/human-readable as-is - notably the
# CRoW Act layer's `Descrip` field is verbose legal boilerplate about byelaw
# exclusions, not a habitat name, so that layer gets a fixed friendly label
# instead of whatever text happens to be in its attributes.
_LAYER_DESCRIPTIONS = {
    "priority_habitats_inventory": lambda attrs: attrs.get("MainHabs"),
    "moorland_change_map": lambda attrs: (
        f"recorded moorland-change area ({attrs['LOCATION'].replace('_', ' ')})"
        if attrs.get("LOCATION")
        else "recorded moorland-change area"
    ),
    "crow_act_access_layer": lambda attrs: "open-access moorland/heath/down (CRoW Act 2000)",
}


def _habitat_context(match_entry):
    descriptions = []
    for layer in match_entry.get("layer_results", []):
        if not layer.get("matched"):
            continue
        formatter = _LAYER_DESCRIPTIONS.get(layer["layer"])
        text = formatter(layer.get("attributes") or {}) if formatter else layer["layer"]
        if text and text not in descriptions:
            descriptions.append(text)
    if not descriptions:
        return None
    return "Within mapped habitat: " + "; ".join(descriptions)


def build_incident_feature(bird, source_info, events, matches_by_id, review_by_ref_id):
    coords = bird.get("coordinates")
    geometry = {"type": "Point", "coordinates": [coords["lon"], coords["lat"]]} if coords else None

    properties = {
        "bird_id": bird["bird_id"],
        "bird_name": bird.get("name") or "",
        "species": "hen_harrier",
        "status": bird.get("status"),
        "status_label": STATUS_LABELS.get(bird.get("status"), bird.get("status")),
        "event_date": bird.get("date_last_contact"),
        "publish_tier": "located",
        "habitat_context": None,
        "location_precision_m": (coords or {}).get("precision_m"),
        "location_disclosed": coords is not None,
        "region_text": bird.get("location_text") or None,
        "source_name": "Natural England hen harrier tracking update",
        "source_update_label": source_info.get("update_label"),
        "source_url": source_info.get("source_url"),
        "confirmed_at": None,
    }

    latest_event = _latest_event_for_bird(events, bird["bird_id"])
    if latest_event is not None:
        match_id = f"match_{latest_event['event_id']}"
        match_entry = matches_by_id.get(match_id)
        review_item = review_by_ref_id.get(match_id)
        if match_entry and review_item and review_item["status"] == STATUS_CONFIRMED_LOCATED:
            properties["publish_tier"] = "confirmed-located"
            properties["habitat_context"] = _habitat_context(match_entry)
            properties["confirmed_at"] = review_item.get("reviewed_at")

    return {"type": "Feature", "geometry": geometry, "properties": properties}


def build_incidents_geojson():
    birds_data = load_json(BIRDS_JSON_PATH, {"source": {}, "birds": {}})
    events = load_json(EVENTS_JSON_PATH, {"events": []})["events"]
    matches_by_id = load_json(MATCHES_JSON_PATH, {"matches": {}})["matches"]
    review_data = load_review_queue()
    review_by_ref_id = {item["ref_id"]: item for item in review_data["items"].values()}

    features = [
        build_incident_feature(bird, birds_data.get("source", {}), events, matches_by_id, review_by_ref_id)
        for bird in birds_data["birds"].values()
    ]
    return {
        "type": "FeatureCollection",
        "generated_at": raw_snapshot.utcnow_iso(),
        "properties": {"schema_version": 1},
        "features": features,
    }


def build_unconfirmed_reports():
    """Citation-only republication: title/source/link/date only - never the pipeline's own
    inferred bird/location match (doc §5/§11 - this is aggregation of RPUK/NE's own public
    reporting, not this project's own assertion)."""
    data = load_json(UNCONFIRMED_REPORTS_PATH, {"reports": {}})
    reports = sorted(data["reports"].values(), key=lambda r: r.get("published_at", ""), reverse=True)
    return {
        "generated_at": raw_snapshot.utcnow_iso(),
        "reports": [
            {
                "title": r["title"],
                "source_name": r["source_name"],
                "link": r["link"],
                "published_at": r.get("published_at"),
            }
            for r in reports
        ],
    }


def build_source_status():
    status = load_fetch_status()
    return {"checked_at": raw_snapshot.utcnow_iso(), "sources": status}


def copy_published_to_site():
    os.makedirs(SITE_DATA_DIR, exist_ok=True)
    for filename in os.listdir(PUBLISHED_DIR):
        shutil.copy2(os.path.join(PUBLISHED_DIR, filename), os.path.join(SITE_DATA_DIR, filename))


def main():
    write_json(os.path.join(PUBLISHED_DIR, "incidents.geojson"), build_incidents_geojson())
    write_json(os.path.join(PUBLISHED_DIR, "unconfirmed_reports.json"), build_unconfirmed_reports())
    write_json(os.path.join(PUBLISHED_DIR, "source_status.json"), build_source_status())
    copy_published_to_site()
    print(f"Built site data -> {SITE_DATA_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
