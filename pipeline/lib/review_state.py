"""
Safe read/update helpers for data/review/review_queue.json - the human
review gate (doc §5). This is the legally load-bearing part of the design:
status only ever changes via a human looking at the evidence and flipping
it (directly, or via pipeline/review.py) - no fetcher or detector script
ever sets anything other than `pending`.
"""

import json
import os

from . import raw_snapshot

REVIEW_QUEUE_PATH = os.path.join(raw_snapshot.repo_root(), "data", "review", "review_queue.json")
SCHEMA_VERSION = 1

STATUS_PENDING = "pending"
STATUS_CONFIRMED_LOCATED = "confirmed-located"
STATUS_CONFIRMED_ATTRIBUTED = "confirmed-attributed"  # reserved for Phase 3 - unused/unreachable here
STATUS_INSUFFICIENT_EVIDENCE = "insufficient-evidence"
STATUS_FALSE_POSITIVE = "false-positive"

# Statuses settable at this phase - confirmed-attributed needs Stage E/Phase 3
# ownership data that doesn't exist yet, so it's deliberately excluded here.
SETTABLE_STATUSES = {STATUS_PENDING, STATUS_CONFIRMED_LOCATED, STATUS_INSUFFICIENT_EVIDENCE, STATUS_FALSE_POSITIVE}

_TIER_LABELS = {
    "within_mapped_moorland_habitat": "intersects mapped moorland/upland habitat",
    "no_habitat_match": "no habitat-layer match",
    "location_not_disclosed": "location not disclosed by NE",
    "location_unparseable": "location field could not be parsed",
}


def load_review_queue():
    if not os.path.exists(REVIEW_QUEUE_PATH):
        return {"schema_version": SCHEMA_VERSION, "generated_at": None, "items": {}}
    with open(REVIEW_QUEUE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_review_queue(data):
    os.makedirs(os.path.dirname(REVIEW_QUEUE_PATH), exist_ok=True)
    with open(REVIEW_QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def _summarize(match_entry, event):
    tier_label = _TIER_LABELS.get(match_entry["confidence_tier"], match_entry["confidence_tier"])
    status_change = ""
    if event.get("event_type") == "status_change":
        status_change = f"{event.get('previous_status')} -> {event.get('new_status')}, "
    return (
        f"{event.get('bird_name') or event['bird_id']} ({event['bird_id']}): {status_change}"
        f"{event.get('source_update_label', '')}. {tier_label}."
    )


def ensure_pending_review(match_entry, event):
    """
    Create a `pending` review_queue.json entry for this geolocation match if
    one doesn't already exist. Idempotent/additive only - never overwrites
    an existing entry, since a human may already have reviewed it.
    """
    data = load_review_queue()
    review_id = f"rq_{match_entry['match_id']}"
    if review_id in data["items"]:
        return data["items"][review_id]

    entry = {
        "review_id": review_id,
        "item_type": "moorland_match",
        "ref_id": match_entry["match_id"],
        "bird_id": event["bird_id"],
        "bird_name": event.get("bird_name", ""),
        "summary": _summarize(match_entry, event),
        "confidence_tier": match_entry["confidence_tier"],
        "evidence_links": [
            f"data/processed/moorland_matches.json#{match_entry['match_id']}",
            event.get("raw_snapshot_path", ""),
        ],
        "status": STATUS_PENDING,
        "status_history": [{"status": STATUS_PENDING, "at": raw_snapshot.utcnow_iso(), "by": "system"}],
        "reviewed_at": None,
        "reviewed_by": None,
        "reviewer_notes": None,
    }
    data["items"][review_id] = entry
    data["generated_at"] = raw_snapshot.utcnow_iso()
    write_review_queue(data)
    return entry


def set_status(review_id, new_status, reviewed_by=None, reviewer_notes=None):
    """
    Flip a review item's status - the actual human review action. Called by
    a person (directly or via pipeline/review.py) after looking at the
    evidence; never called automatically by a fetcher or detector.
    """
    if new_status not in SETTABLE_STATUSES:
        raise ValueError(f"{new_status!r} is not settable at this build phase (allowed: {sorted(SETTABLE_STATUSES)})")
    data = load_review_queue()
    if review_id not in data["items"]:
        raise KeyError(f"No review item {review_id!r} found.")
    entry = data["items"][review_id]
    entry["status"] = new_status
    entry["reviewed_at"] = raw_snapshot.utcnow_iso()
    entry["reviewed_by"] = reviewed_by
    entry["reviewer_notes"] = reviewer_notes
    entry["status_history"].append({"status": new_status, "at": entry["reviewed_at"], "by": reviewed_by or "unknown"})
    data["generated_at"] = raw_snapshot.utcnow_iso()
    write_review_queue(data)
    return entry
