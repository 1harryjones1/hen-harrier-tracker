#!/usr/bin/env python
"""
Stage D: geolocation cross-reference against moorland/upland-habitat layers.

For each bird_events.json status-change entry where the bird is now dead or
missing-fate-unknown, and which has a resolvable last-known coordinate,
queries the 3 habitat FeatureServer layers (server-side point-in-polygon,
see lib/arcgis_client.py) and records a confidence tier. This is the one
inference this pipeline makes beyond raw source facts, so every result also
gets a `pending` entry in review_queue.json (lib/review_state.py) - nothing
here is published as "confirmed" until a human flips that status.

Deliberately does not fetch or cache the Who Owns England estate-boundary
layer anywhere - that's Stage E/Phase 3 (ownership unmask) machinery, out of
scope here, and carries its own unresolved licensing question (doc §11).
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.arcgis_client import ArcGISError, query_point
from lib.review_state import ensure_pending_review

EVENTS_JSON_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "bird_events.json")
MATCHES_JSON_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "moorland_matches.json")
CONFIG_PATH = os.path.join(raw_snapshot.repo_root(), "pipeline", "config", "arcgis_layers.json")
SCHEMA_VERSION = 1

RELEVANT_STATUSES = {"dead", "missing_fate_unknown"}
RESOLVABLE_LOCATION_STATUSES = {"grid_reference", "decimal_latlon"}
LAYER_KEYS = ["priority_habitats_inventory", "moorland_change_map", "crow_act_access_layer"]


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


def _confidence_tier(location_status, layer_results):
    if location_status not in RESOLVABLE_LOCATION_STATUSES:
        return "location_unparseable" if location_status == "unparseable" else "location_not_disclosed"
    if any(r["matched"] for r in layer_results):
        return "within_mapped_moorland_habitat"
    return "no_habitat_match"


def match_event(event, layers_config):
    """Query all 3 habitat layers for one event's coordinates. Returns a moorland_matches.json entry dict."""
    location_status = event.get("location_status")
    coords = event.get("coordinates")
    match_id = f"match_{event['event_id']}"

    if location_status not in RESOLVABLE_LOCATION_STATUSES or not coords:
        return {
            "match_id": match_id,
            "event_id": event["event_id"],
            "bird_id": event["bird_id"],
            "queried_at": raw_snapshot.utcnow_iso(),
            "query_coordinates": None,
            "confidence_tier": _confidence_tier(location_status, []),
            "layer_results": [],
        }

    layer_results = []
    for layer_key in LAYER_KEYS:
        layer_info = layers_config.get(layer_key)
        if layer_info is None:
            layer_results.append({
                "layer": layer_key, "feature_server_url": None, "matched": False,
                "attributes": None, "raw_snapshot_path": None,
                "error": "layer not yet discovered - run fetch_moorland_change.py",
            })
            continue
        try:
            response = query_point(layer_info["url"], lat=coords["lat"], lon=coords["lon"])
        except (ArcGISError, requests.RequestException) as exc:
            layer_results.append({
                "layer": layer_key, "feature_server_url": layer_info["url"], "matched": False,
                "attributes": None, "raw_snapshot_path": None, "error": str(exc),
            })
            continue

        filename = f"{raw_snapshot.utcnow_compact()}-{event['bird_id']}-{layer_key}.json"
        raw_path, _ = raw_snapshot.write_raw("habitat_queries", filename, json.dumps(response, indent=2))
        features = response.get("features", [])
        matched = len(features) > 0
        layer_results.append({
            "layer": layer_key,
            "feature_server_url": layer_info["url"],
            "matched": matched,
            "attributes": features[0]["properties"] if matched else None,
            "raw_snapshot_path": raw_snapshot.relative_path(raw_path),
        })

    return {
        "match_id": match_id,
        "event_id": event["event_id"],
        "bird_id": event["bird_id"],
        "queried_at": raw_snapshot.utcnow_iso(),
        "query_coordinates": coords,
        "confidence_tier": _confidence_tier(location_status, layer_results),
        "layer_results": layer_results,
    }


def main():
    events_data = load_json(EVENTS_JSON_PATH, {"schema_version": SCHEMA_VERSION, "events": []})
    matches_data = load_json(
        MATCHES_JSON_PATH, {"schema_version": SCHEMA_VERSION, "generated_at": None, "matches": {}}
    )
    layers_config = load_json(CONFIG_PATH, {})

    # location_updated/new_bird_tagged events don't carry new_status, so this
    # naturally selects only status_change events landing on dead/missing.
    relevant_events = [e for e in events_data["events"] if e.get("new_status") in RELEVANT_STATUSES]

    new_count = 0
    for event in relevant_events:
        match_id = f"match_{event['event_id']}"
        if match_id in matches_data["matches"]:
            continue
        entry = match_event(event, layers_config)
        matches_data["matches"][match_id] = entry
        ensure_pending_review(entry, event)
        new_count += 1

    matches_data["generated_at"] = raw_snapshot.utcnow_iso()
    write_json(MATCHES_JSON_PATH, matches_data)

    print(f"Geolocation cross-reference: {new_count} new match(es), {len(matches_data['matches'])} total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
