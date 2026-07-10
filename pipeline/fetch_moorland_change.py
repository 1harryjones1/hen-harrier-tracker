#!/usr/bin/env python
"""
Discovers the current FeatureServer URL for each of the 3 habitat layers
used in Stage D (Moorland Change Map, Priority Habitats Inventory, CRoW Act
2000 - Access Layer), caching them to pipeline/config/arcgis_layers.json.

This file is committed so changes are diffable: the Moorland Change Map is
republished under a new dataset name every year (doc §3/§9), so re-running
this monthly and committing any change makes that self-healing and visible,
instead of a silent 404 discovered months later. Runs all 3 discoveries
independently - one layer's failure doesn't block reporting the others.
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.arcgis_client import ArcGISError, discover_layer
from lib.fetch_status import record_fetch_result

CONFIG_PATH = os.path.join(raw_snapshot.repo_root(), "pipeline", "config", "arcgis_layers.json")
SOURCE_NAME = "arcgis_habitat_layers"

LAYERS = [
    {"key": "moorland_change_map", "search_title": "Moorland Change Map", "exact_title_prefix": None},
    {"key": "priority_habitats_inventory", "search_title": "Priority Habitats Inventory", "exact_title_prefix": None},
    {"key": "crow_act_access_layer", "search_title": "CRoW Act", "exact_title_prefix": "CRoW Act 2000 - Access Layer"},
]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def main():
    config = load_config()
    changed_keys = []
    errors = []

    for layer in LAYERS:
        try:
            discovered = discover_layer(layer["search_title"], exact_title_prefix=layer["exact_title_prefix"])
        except (ArcGISError, requests.RequestException) as exc:
            errors.append(f"{layer['key']}: {exc}")
            continue

        discovered["discovered_at"] = raw_snapshot.utcnow_iso()
        previous = config.get(layer["key"])
        if previous is None or previous.get("item_id") != discovered["item_id"] or previous.get("url") != discovered["url"]:
            changed_keys.append(layer["key"])
        config[layer["key"]] = discovered

    write_config(config)

    if errors:
        record_fetch_result(SOURCE_NAME, ok=False, detail="; ".join(errors))
        print("FAILED for some layers:\n  " + "\n  ".join(errors), file=sys.stderr)
    else:
        record_fetch_result(SOURCE_NAME, ok=True, detail=f"{len(LAYERS)} layers discovered, {len(changed_keys)} changed")

    print(f"Layer discovery changed for: {changed_keys}" if changed_keys else "All layers unchanged.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
