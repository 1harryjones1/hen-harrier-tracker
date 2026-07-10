# Hen Harrier Tracker

Automated, transparently-sourced tracking of UK satellite-tagged hen harrier status,
cross-referenced against mapped moorland/upland habitat. Every published item traces
back to a dated, linked source. Runs entirely on GitHub Actions + Pages, at $0/month.

This README is both the project's public methodology page and its developer setup
guide — the first half explains what the site shows and why; [Running this
project](#running-this-project) covers the code.

## What this is

Natural England periodically publishes the status of every satellite-tagged hen
harrier in its tracking programme. This project automatically detects status changes
in that table (e.g. a bird going from "alive" to "missing, fate unknown"), checks
whether a bird's last-known location intersects mapped moorland/upland habitat, and
publishes the result as a map — with a mandatory human review step before any habitat
match is shown, and full source links and timestamps on everything.

## What this is not

- **Not ownership or estate attribution.** This build deliberately stops at "near
  mapped moorland/upland habitat" and never names an estate or landowner. That
  requires a further stage (Land Registry / Companies House lookups) that needs its
  own credentials and explicit legal sign-off before it ships — it's designed for,
  but not built or live, in this version.
- **Not real-time.** This is a scheduled batch pipeline. See the site's own "data
  freshness" footer for each source's actual refresh cadence.
- **Not a replication of** Murgatroyd et al. 2019 (*"Patterns of satellite tagged hen
  harrier disappearances suggest widespread illegal killing on British grouse
  moors,"* Nature Communications). That study used each bird's full GPS fix history
  and a bespoke grouse-moor land-cover classification, neither of which is part of
  NE's routine public release. This project uses public proxies instead (see below) —
  inspired by that study's question, not a reproduction of its method.
- **Not proof that a habitat match means persecution, or even a grouse moor.** A
  "within mapped moorland habitat" match means exactly that — the bird's last-known
  point intersects a habitat/access-land layer. It is not a designation that the land
  is a grouse moor, and never identifies who owns or manages it.

## Data sources used in this build

| Source | What it gives | Licence | Cadence |
|---|---|---|---|
| [NE hen harrier tracking update](https://www.gov.uk/government/publications/hen-harriers-tracking-programme-update/hen-harrier-tracking-update) | Per-bird status table (tag ID, status, approximate last location) | OGL v3.0, Crown copyright | Irregular, historically every ~8 weeks to 6 months |
| [Natural England blog](https://naturalengland.blog.gov.uk/feed) | Ad hoc narrative updates, sometimes faster than the table | OGL v3.0 | Ad hoc |
| [Raptor Persecution UK](https://raptorpersecutionuk.org/feed) | Independent investigative reporting; often the first public mention of an incident, and for Scotland sometimes the only one | Site copyright — linked/quoted under fair dealing, not republished | Near-daily during incidents |
| [Priority Habitats Inventory (England)](https://naturalengland-defra.opendata.arcgis.com/datasets/Defra::priority-habitats-inventory-england/about) | Baseline upland/moorland habitat extent | OGL v3.0 (a small component is CC-BY 4.0, Cumbria Biodiversity Data Centre) | Infrequent |
| [Moorland Change Map (England)](https://naturalengland-defra.opendata.arcgis.com/datasets/Defra::moorland-change-map-england-2024-2025/about) | Recorded moorland change/burning activity | OGL v3.0 | Annual, republished under a new name each year |
| [CRoW Act 2000 - Access Layer](https://naturalengland-defra.opendata.arcgis.com/datasets/Defra::crow-act-2000-access-layer/about) | Open-access mountain/moor/heath/down extent | OGL v3.0 | Infrequent |

## How it works

1. **Fetch.** Scheduled jobs pull each source and write an immutable, timestamped raw
   snapshot to `data/raw/` — every published claim traces back to an exact file with
   an exact fetch time.
2. **Parse.** The NE table is parsed tolerantly: its schema has drifted over the
   programme's history, and the location field is genuinely polymorphic (a grid
   reference, a redacted note, "N/A" for a bird with no fixed point, or free-text
   decimal coordinates for a bird that ended up outside Great Britain).
3. **Diff.** Each new snapshot is compared against the last one. A status change
   becomes an event in an append-only log — nothing is ever edited after the fact,
   only added to.
4. **Cross-reference.** For a bird that's now dead or missing, its last-known point is
   checked against the three habitat layers above (server-side, no bulk data
   download) and given a confidence tier.
5. **Human review.** A habitat match is the one inference this pipeline makes beyond
   raw source facts, so it sits as `pending` until a person reviews the evidence and
   explicitly confirms it (`pipeline/review.py`, or a direct edit + commit to
   `data/review/review_queue.json`). Nothing publishes with habitat context attached
   until that happens.
6. **Publish.** `pipeline/build_site.py` merges confirmed reviews into
   `data/published/`, which is the only thing the static site reads.

Official NE status changes publish immediately with source + timestamp (Natural
England's own data, no misattribution risk). Press mentions (RPUK / NE blog) show
separately as citation-only "unconfirmed reports" — title, source, link, date — never
this pipeline's own inferred match to a specific bird or location.

## Why some locations are missing or vague

Roughly a third of tagged birds have no usable coordinate at any given time, for two
different reasons that matter:

- **Deliberately withheld.** Natural England does not publish precise roost/nest sites
  for some birds, for species-protection reasons. This is intentional, not a gap in
  this pipeline.
- **Not yet fixed.** A currently-alive, mobile bird may simply not have a published
  point ("N/A" in the source). Also intentional, also not a bug.

Both cases show `geometry: null` in the published data and appear in the site's
"locations not disclosed" list rather than a fabricated map point.

Separately: a location that looks structurally valid but resolves somewhere
implausible (checked against a rough latitude-banded bounding region for Great
Britain) is treated as unparseable rather than trusted — this catches likely data
entry errors in the source table (a single mistyped grid-reference letter can place a
point hundreds of kilometres out to sea) without needing a full coastline model.

## Licensing and attribution

- Natural England data: Open Government Licence v3.0, © Crown copyright.
- Priority Habitats Inventory: OGL v3.0, except a component sourced from Cumbria
  Biodiversity Data Centre under CC-BY 4.0.
- Raptor Persecution UK content is independently copyrighted; this project links and
  quotes headlines under fair dealing and does not republish full posts.
- The code in this repository (everything under `pipeline/` and `site/`) is MIT
  licensed — see `LICENSE`. That licence does not extend to the underlying data.

## Roadmap

This build covers Phase 0 (foundations), Phase 1 (status-change detection + press
monitoring), and Phase 2 (habitat cross-referencing). Not built in this version, by
design:

- **Ownership unmask** (Land Registry / Companies House) and the `confirmed-attributed`
  review tier — needs API credentials this build doesn't have, and the architecture
  doc is explicit that publication policy here needs a human/legal sign-off before it
  ships.
- **Statistical layer** comparing habitat-match rates against a baseline expectation.
- **Scotland expansion, other species, subscriber alerts.**

---

## Running this project

### Setup

```
python -m venv .venv
.venv/Scripts/pip install -r pipeline/requirements.txt      # Windows
# source .venv/bin/activate && pip install -r pipeline/requirements.txt   # macOS/Linux
```

No API keys are needed for anything in this build — every source used is open data
with no registration required.

### Running the pipeline manually

```
python pipeline/fetch_hen_harrier_tags.py    # Stage A: scrape gov.uk, download the current .ods
python pipeline/diff_and_detect.py           # Stage B+C: parse it, diff against the last run, log events
python pipeline/geolocate_match.py           # Stage D: cross-reference against habitat layers
python pipeline/fetch_moorland_change.py     # refresh the cached habitat layer URLs (run monthly)
python pipeline/fetch_ne_blog.py             # poll the NE blog for harrier-relevant posts
python pipeline/fetch_rpuk_rss.py            # poll Raptor Persecution UK for the same
python pipeline/build_site.py                # merge everything into data/published/ and site/data/
```

### Reviewing pending habitat matches

```
python pipeline/review.py list --status pending
python pipeline/review.py show <review_id>
python pipeline/review.py confirm-located <review_id> --by "Your Name" --notes "..."
python pipeline/review.py reject <review_id> --by "Your Name" --notes "..."       # false positive
python pipeline/review.py hold <review_id> --by "Your Name" --notes "..."         # insufficient evidence
python pipeline/build_site.py    # re-run to publish the change
```

Hand-editing `data/review/review_queue.json` directly and committing is equally
valid — the CLI just reduces the chance of a typo'd status.

### Viewing the site locally

```
python -m http.server 8765 --directory site
```

then open `http://localhost:8765`.

### Deploying

This repo is not yet connected to a GitHub remote. To go live: push it to a **public**
GitHub repository (private repos don't get unmetered free Actions minutes), then in
the repo's Settings: enable "Read and write permissions" for the default
`GITHUB_TOKEN` under Actions → General, and set Pages → Source to "GitHub Actions".
The five workflows under `.github/workflows/` handle everything else on their own
schedules.

### Repository layout

```
data/
  raw/          immutable, timestamped snapshots of every fetch - the provenance trail
  processed/    current parsed state (birds.json, bird_events.json, moorland_matches.json, ...)
  review/       the human review gate (review_queue.json)
  published/    only what's cleared for public display - the only thing the site reads
pipeline/       all fetchers, parsers, and the diff/geolocate/build/review scripts
site/           static dashboard - plain HTML/CSS/JS, Leaflet via CDN, no build step
.github/workflows/   scheduled fetch/build/deploy jobs (inert until pushed to GitHub)
```

## Corrections and contact

If something on the map is wrong, or a source has changed in a way this pipeline
mishandled, please open an issue on this repository with a link to the specific
entry and what looks incorrect.
