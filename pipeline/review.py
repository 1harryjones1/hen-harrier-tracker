#!/usr/bin/env python
"""
Small CLI for the human review gate (doc §5). Hand-editing
data/review/review_queue.json directly and committing is equally valid -
this just reduces the chance of a typo'd status or a forgotten
status_history entry for the same everyday actions.

Usage:
  python pipeline/review.py list [--status pending]
  python pipeline/review.py show <review_id>
  python pipeline/review.py confirm-located <review_id> --by "Your Name" [--notes "..."]
  python pipeline/review.py reject <review_id> --by "Your Name" --notes "..."
  python pipeline/review.py hold <review_id> --by "Your Name" --notes "..."

`confirm-located` publishes the habitat-overlap annotation (region/habitat
context, no owner named) - the only tier reachable at this build phase.
There is no `confirm-attributed` command: that tier needs Stage E/Phase 3
ownership data that doesn't exist yet.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.review_state import (
    STATUS_CONFIRMED_LOCATED,
    STATUS_FALSE_POSITIVE,
    STATUS_INSUFFICIENT_EVIDENCE,
    load_review_queue,
    set_status,
)


def cmd_list(args):
    data = load_review_queue()
    items = list(data["items"].values())
    if args.status:
        items = [i for i in items if i["status"] == args.status]
    items.sort(key=lambda i: i["review_id"])
    if not items:
        print("(no matching review items)")
        return 0
    for item in items:
        print(f"{item['review_id']:40} [{item['status']:20}] {item['summary']}")
    return 0


def cmd_show(args):
    data = load_review_queue()
    item = data["items"].get(args.review_id)
    if item is None:
        print(f"No review item {args.review_id!r} found.", file=sys.stderr)
        return 1
    import json
    print(json.dumps(item, indent=2))
    return 0


def _set(args, status):
    try:
        entry = set_status(args.review_id, status, reviewed_by=args.by, reviewer_notes=args.notes)
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"{entry['review_id']} -> {entry['status']} (by {entry['reviewed_by'] or 'unknown'})")
    print("Re-run pipeline/build_site.py to publish this change.")
    return 0


def cmd_confirm_located(args):
    return _set(args, STATUS_CONFIRMED_LOCATED)


def cmd_reject(args):
    return _set(args, STATUS_FALSE_POSITIVE)


def cmd_hold(args):
    return _set(args, STATUS_INSUFFICIENT_EVIDENCE)


def main():
    parser = argparse.ArgumentParser(description="Human review gate CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List review items")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one review item in full")
    p_show.add_argument("review_id")
    p_show.set_defaults(func=cmd_show)

    for name, func, help_text in [
        ("confirm-located", cmd_confirm_located, "Publish with region/habitat context, no owner named"),
        ("reject", cmd_reject, "Mark as a false positive - won't be published"),
        ("hold", cmd_hold, "Mark as insufficient evidence - won't be published, kept for later"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("review_id")
        p.add_argument("--by", default=None, help="Reviewer name")
        p.add_argument("--notes", default=None, help="Reviewer notes")
        p.set_defaults(func=func)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
