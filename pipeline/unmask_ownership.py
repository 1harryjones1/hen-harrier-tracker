#!/usr/bin/env python
"""
Stage E: ownership-candidate surfacing.

Only runs for review items that are ALREADY confirmed-located (a human has
already confirmed the habitat match) - never for merely-pending or rejected
ones. Runs the chain: resolve local authority -> find INSPIRE title number
-> look up CCOD/OCOD -> Companies House officers/PSC. Writes small,
already-matched evidence snapshots and a pending `ownership_match` review
item at whichever point the chain succeeds or honestly stops.

Hard scope boundary: this script only ever writes to
data/processed/ownership_matches.json and data/review/review_queue.json.
It does not touch, and must never touch, pipeline/build_site.py or anything
under data/published/ - naming an estate/owner publicly is a distinct,
future, separate decision requiring real legal input, not a byproduct of
running this script. See the plan this was built from for the reasoning.

CCOD/OCOD bulk CSVs are expected at .cache/land_registry_bulk/ccod.csv and
.cache/land_registry_bulk/ocod.csv (gitignored, never committed - override
with the CCOD_CSV_PATH / OCOD_CSV_PATH environment variables). If neither
file exists yet, every match honestly reports
"ownership_structure_not_automatable" - wrong the moment a real account is
sorted, but never silently wrong in the meantime.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import raw_snapshot
from lib.companies_house_client import (
    CompaniesHouseError,
    get_officers,
    get_persons_with_significant_control,
    has_protected_psc,
)
from lib.land_registry_client import LandRegistryError, find_title_number, lookup_ccod_ocod, resolve_local_authority
from lib.review_state import STATUS_CONFIRMED_LOCATED, ensure_pending_ownership_review, load_review_queue

REPO_ROOT = raw_snapshot.repo_root()
MOORLAND_MATCHES_PATH = os.path.join(REPO_ROOT, "data", "processed", "moorland_matches.json")
OWNERSHIP_MATCHES_PATH = os.path.join(REPO_ROOT, "data", "processed", "ownership_matches.json")
SCHEMA_VERSION = 1

CCOD_PATH = os.environ.get("CCOD_CSV_PATH", os.path.join(REPO_ROOT, ".cache", "land_registry_bulk", "ccod.csv"))
OCOD_PATH = os.environ.get("OCOD_CSV_PATH", os.path.join(REPO_ROOT, ".cache", "land_registry_bulk", "ocod.csv"))


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


def _snapshot(bird_id, step, payload):
    filename = f"{raw_snapshot.utcnow_compact()}-{bird_id}-{step}.json"
    path, _ = raw_snapshot.write_raw("ownership_queries", filename, json.dumps(payload, indent=2, default=str))
    return raw_snapshot.relative_path(path)


def build_ownership_match(moorland_match, bird_id):
    event_id = moorland_match["event_id"]
    match_id = f"ownership_match_{event_id}"
    coords = moorland_match.get("query_coordinates")
    chain = []

    if not coords:
        chain.append({"step": "local_authority_resolution", "matched": False, "attributes": None})
        return _entry(match_id, event_id, moorland_match["match_id"], coords, "no_local_authority_resolved", chain)

    try:
        la = resolve_local_authority(coords["lat"], coords["lon"])
    except LandRegistryError as exc:
        chain.append({"step": "local_authority_resolution", "matched": False, "attributes": None, "error": str(exc)})
        return _entry(match_id, event_id, moorland_match["match_id"], coords, "no_local_authority_resolved", chain)
    if la is None:
        chain.append({"step": "local_authority_resolution", "matched": False, "attributes": None})
        return _entry(match_id, event_id, moorland_match["match_id"], coords, "no_local_authority_resolved", chain)
    chain.append({
        "step": "local_authority_resolution", "matched": True, "attributes": la,
        "raw_snapshot_path": _snapshot(bird_id, "la", la),
    })

    try:
        title_number = find_title_number(coords["lat"], coords["lon"], la["code"])
    except LandRegistryError as exc:
        # Distinct from "genuinely no title found" (see below) - this means we
        # haven't downloaded/configured this LA's INSPIRE file yet, an
        # operational gap, not a finding about the land itself.
        chain.append({"step": "inspire_title_lookup", "matched": False, "attributes": None, "error": str(exc)})
        return _entry(
            match_id, event_id, moorland_match["match_id"], coords, "local_authority_gml_not_configured", chain
        )
    if title_number is None:
        chain.append({"step": "inspire_title_lookup", "matched": False, "attributes": None})
        return _entry(match_id, event_id, moorland_match["match_id"], coords, "no_title_registered", chain)
    chain.append({
        "step": "inspire_title_lookup", "matched": True, "attributes": {"title_number": title_number},
        "raw_snapshot_path": _snapshot(bird_id, "title", {"title_number": title_number}),
    })

    ccod_result = lookup_ccod_ocod(title_number, ccod_path=CCOD_PATH, ocod_path=OCOD_PATH)
    if ccod_result is None:
        chain.append({"step": "ccod_ocod_lookup", "matched": False, "attributes": None})
        return _entry(
            match_id, event_id, moorland_match["match_id"], coords, "ownership_structure_not_automatable", chain
        )
    proprietors = ccod_result["proprietors"]
    chain.append({
        "step": "ccod_ocod_lookup", "matched": True,
        "attributes": {"dataset": ccod_result["dataset"], "proprietor_count": len(proprietors)},
        "raw_snapshot_path": _snapshot(bird_id, "ccod_ocod", ccod_result),
    })

    company_number = next((p["company_registration_no"] for p in proprietors if p["company_registration_no"]), None)
    if not company_number:
        return _entry(match_id, event_id, moorland_match["match_id"], coords, "company_number_unavailable", chain)

    try:
        officers = get_officers(company_number)
    except CompaniesHouseError as exc:
        chain.append({"step": "companies_house_officers", "matched": False, "attributes": None, "error": str(exc)})
        return _entry(
            match_id, event_id, moorland_match["match_id"], coords, "company_not_found_at_companies_house", chain
        )
    if officers is None:
        chain.append({"step": "companies_house_officers", "matched": False, "attributes": None})
        return _entry(
            match_id, event_id, moorland_match["match_id"], coords, "company_not_found_at_companies_house", chain
        )
    officer_items = officers.get("items", [])
    chain.append({
        "step": "companies_house_officers", "matched": len(officer_items) > 0,
        "attributes": {"officer_count": len(officer_items)},
        "raw_snapshot_path": _snapshot(bird_id, "officers", officers),
    })

    try:
        psc = get_persons_with_significant_control(company_number)
    except CompaniesHouseError as exc:
        psc = None
        chain.append({"step": "companies_house_psc", "matched": False, "attributes": None, "error": str(exc)})
    else:
        psc_items = (psc or {}).get("items", [])
        chain.append({
            "step": "companies_house_psc", "matched": len(psc_items) > 0,
            "attributes": {"psc_count": len(psc_items), "has_protected_psc": has_protected_psc(psc)},
            "raw_snapshot_path": _snapshot(bird_id, "psc", psc) if psc else None,
        })

    if has_protected_psc(psc):
        tier = "controlling_party_protected"
    elif not officer_items and not (psc or {}).get("items"):
        tier = "company_found_no_controlling_parties_disclosed"
    else:
        tier = "officers_and_controlling_party_found"

    return _entry(match_id, event_id, moorland_match["match_id"], coords, tier, chain)


def _entry(match_id, event_id, moorland_match_id, coords, tier, chain):
    return {
        "match_id": match_id,
        "event_id": event_id,
        "moorland_match_id": moorland_match_id,
        "queried_at": raw_snapshot.utcnow_iso(),
        "query_coordinates": coords,
        "confidence_tier": tier,
        "chain": chain,
    }


def main():
    review_data = load_review_queue()
    moorland_matches = load_json(MOORLAND_MATCHES_PATH, {"matches": {}})["matches"]
    ownership_data = load_json(
        OWNERSHIP_MATCHES_PATH, {"schema_version": SCHEMA_VERSION, "generated_at": None, "matches": {}}
    )

    confirmed_moorland_items = [
        item for item in review_data["items"].values()
        if item["item_type"] == "moorland_match" and item["status"] == STATUS_CONFIRMED_LOCATED
    ]

    new_count = 0
    for review_item in confirmed_moorland_items:
        moorland_match = moorland_matches.get(review_item["ref_id"])
        if moorland_match is None:
            continue
        match_id = f"ownership_match_{moorland_match['event_id']}"
        if match_id in ownership_data["matches"]:
            continue

        entry = build_ownership_match(moorland_match, review_item["bird_id"])
        ownership_data["matches"][match_id] = entry
        ensure_pending_ownership_review(entry, review_item)
        new_count += 1
        print(f"  {review_item.get('bird_name') or review_item['bird_id']}: {entry['confidence_tier']}")

    ownership_data["generated_at"] = raw_snapshot.utcnow_iso()
    write_json(OWNERSHIP_MATCHES_PATH, ownership_data)

    print(f"Ownership candidate surfacing: {new_count} new, {len(ownership_data['matches'])} total.")
    print("Reminder: nothing here is published - review with pipeline/review.py, build_site.py never reads this file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
