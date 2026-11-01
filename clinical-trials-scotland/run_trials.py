#!/usr/bin/env python3
"""One-click launcher: fetch the registries, build the digest, copy it and open it.

Runs entirely on your own machine and uses no Claude usage.
Put your private profile next to this file as profile.json (it is git-ignored).
Output goes to the out/ folder (also git-ignored).
"""
import os
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

days = sys.argv[1] if len(sys.argv) > 1 else "7"
digest = os.path.join(OUT, "digest.md")
cmd = [
    sys.executable, os.path.join(HERE, "scotland_trials.py"),
    "--days", days,
    "--markdown", digest,
    "--details", os.path.join(OUT, "details.txt"),
    "--json", os.path.join(OUT, "trials.json"),
]
profile = os.path.join(HERE, "profile.json")
if os.path.exists(profile):
    cmd += ["--profile", profile]
else:
    print("No profile.json found: showing studies for everyone, with no distance or exclusion flags.")

print(f"Fetching studies from the last {days} days (about 30 seconds)...")
if subprocess.run(cmd).returncode != 0 or not os.path.exists(digest):
    sys.exit("The scrape failed. See the messages above.")

text = open(digest, encoding="utf-8").read()
for clip in (["clip"], ["pbcopy"], ["xclip", "-selection", "clipboard"]):
    try:
        subprocess.run(clip, input=text.encode("utf-16" if clip[0] == "clip" else "utf-8"), check=True)
        print("Digest copied to the clipboard.")
        break
    except (OSError, subprocess.CalledProcessError):
        continue
print(f"Saved to {digest}")
webbrowser.open("file://" + digest.replace(os.sep, "/"))
