"""
Helper for writing immutable, timestamped raw snapshots to data/raw/.

Filenames use a full UTC timestamp (down to the second) rather than just a
date. The doc's own repo layout suggests one file per calendar date, but a
full timestamp is a strictly more precise reading of "immutable, timestamped
snapshot" and avoids collisions when a source is legitimately fetched more
than once in a day (e.g. local testing, backfilling an older round, or a
retried CI run) - a same-day collision would otherwise silently skip a write
under the "never overwrite" rule.
"""

import os
from datetime import datetime, timezone


def repo_root():
    here = os.path.dirname(os.path.abspath(__file__))  # pipeline/lib
    return os.path.dirname(os.path.dirname(here))  # repo root


def utcnow_compact():
    """UTC timestamp formatted for safe use in filenames (no colons): YYYYMMDDTHHMMSSZ."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utcnow_iso():
    """UTC timestamp in ISO-8601 form, e.g. 2026-07-10T14:32:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_path(source_dir, filename):
    """Return an absolute path under data/raw/<source_dir>/<filename>, ensuring the directory exists."""
    dirpath = os.path.join(repo_root(), "data", "raw", source_dir)
    os.makedirs(dirpath, exist_ok=True)
    return os.path.join(dirpath, filename)


def write_raw(source_dir, filename, content, mode="w"):
    """
    Write `content` to data/raw/<source_dir>/<filename> if it doesn't already
    exist (raw snapshots are immutable - never overwritten once written).

    Returns (path, written: bool).
    """
    path = raw_path(source_dir, filename)
    if os.path.exists(path):
        return path, False
    if "b" in mode:
        with open(path, mode) as f:
            f.write(content)
    else:
        with open(path, mode, encoding="utf-8", newline="") as f:
            f.write(content)
    return path, True


def relative_path(abs_path):
    """Return `abs_path` relative to the repo root, with forward slashes (for portable JSON output)."""
    return os.path.relpath(abs_path, repo_root()).replace(os.sep, "/")
