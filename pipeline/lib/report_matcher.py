"""
Shared candidate-matching + storage for NE blog / RPUK RSS "unconfirmed
reports" (Phase 1's separate feed, doc §10). Deliberately conservative:
gates on whether an entry mentions "harrier" at all before treating it as
relevant, then best-effort matches known tagged-bird nicknames as
internal-only metadata - RSS entity extraction has a "high false-positive
risk" (doc §3), so nothing here is ever treated as confirmed, and only
title/source/link/date are ever shown publicly (build_site.py strips the
rest - doc §5/§11: this is aggregation of the source's own public
reporting, not this project's own assertion).
"""

import json
import os
import re
from datetime import datetime, timezone

from . import raw_snapshot

REPORTS_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "unconfirmed_reports.json")
BIRDS_JSON_PATH = os.path.join(raw_snapshot.repo_root(), "data", "processed", "birds.json")
SCHEMA_VERSION = 1

_HARRIER_RE = re.compile(r"harrier", re.IGNORECASE)


def load_known_bird_names():
    """
    Bird names worth keyword-matching on. Excludes blank/N/A names and
    anything containing a digit (e.g. "R1-F2-22") - those are tag codes,
    not real nicknames, and matching on fragments like "R1" against free
    text would be pure noise.
    """
    if not os.path.exists(BIRDS_JSON_PATH):
        return []
    with open(BIRDS_JSON_PATH, "r", encoding="utf-8") as f:
        birds = json.load(f)["birds"]
    names = set()
    for bird in birds.values():
        name = (bird.get("name") or "").strip()
        if name and name.upper() != "N/A" and len(name) >= 3 and not any(c.isdigit() for c in name):
            names.add(name)
    return sorted(names)


def is_harrier_relevant(text):
    return bool(_HARRIER_RE.search(text or ""))


def match_bird_names(text, known_names):
    return [name for name in known_names if re.search(r"\b" + re.escape(name) + r"\b", text)]


def parsed_time_to_iso(struct_time):
    if struct_time is None:
        return None
    return datetime(*struct_time[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_reports():
    if not os.path.exists(REPORTS_PATH):
        return {"schema_version": SCHEMA_VERSION, "generated_at": None, "reports": {}}
    with open(REPORTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_reports(data):
    os.makedirs(os.path.dirname(REPORTS_PATH), exist_ok=True)
    with open(REPORTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def upsert_reports(new_reports):
    """Merge `new_reports` into the shared processed file, keyed by report_id. Additive/idempotent -
    re-fetching the same feed entry doesn't duplicate it. Returns the number of genuinely new reports."""
    data = load_reports()
    added = 0
    for report in new_reports:
        if report["report_id"] not in data["reports"]:
            data["reports"][report["report_id"]] = report
            added += 1
    data["generated_at"] = raw_snapshot.utcnow_iso()
    write_reports(data)
    return added
