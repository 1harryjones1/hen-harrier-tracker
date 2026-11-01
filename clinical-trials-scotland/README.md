# Scotland clinical trials scraper

Finds clinical trials and research studies that are **recruiting in Scotland right now**,
flags the ones open to healthy volunteers or that mention payment, tags them by topic, and
writes a Slack-ready weekly digest.

It has nothing to do with the hen harrier tracker; it just lives in this repo.

## What it checks

| Source | How | Notes |
| --- | --- | --- |
| [ClinicalTrials.gov](https://clinicaltrials.gov/data-api/api) | API v2, recruiting / not-yet-recruiting studies inside a circle over Scotland plus a "Scotland" location search | Sites are kept only if they fall inside a Scotland polygon or name a Scottish town or health board, and the Scottish site itself is still open |
| [ISRCTN](https://www.isrctn.com/) | Public XML API, `recruitmentCountry:Scotland` (and UK-only records with Scottish centres) whose end date hasn't passed | Recruitment status is worked out from the recruitment dates; the plain-English summary often states payment and ages |

Trials on both registries are shown once. Children-only studies are dropped.
Be Part of Research's API needs approval, and Call for Participants has shut down, so neither is used.

## How a study is labelled

| Label | Meaning |
| --- | --- |
| 💷 paid, healthy volunteers | Accepts healthy volunteers and the record mentions payment (£, voucher, honorarium…) |
| 💷 healthy volunteers, phase 1/industry (often paid) | Healthy volunteers, and it's phase 1 or industry-sponsored |
| 🟡 healthy volunteers (pay not stated) | Healthy volunteers, no payment mentioned |
| 💷 paid, but only for people with the condition | Mentions payment, patients only |
| patients only / expenses covered | Everything else |

Each numbered study also gets a snapshot line pulled from the registry text:

`💷 pay · ⏱ visits, length, hours per visit, overnight stays · 🔬 what's done (drug, bloods, biopsies, scans…) · 🟢/🟠/🔴 rough risk`

It's keyword-based (£ amounts, "four visits over 12 weeks", "muscle biopsy", "PET" and so on), so treat it as a first pass.
`--details details.txt` writes the full "what does it involve" text for each numbered study; the weekly Claude
run uses it to correct the snapshots and to look up pay on recruitment pages.

## Topics

Built in (`DEFAULT_TOPICS` in `scotland_trials.py`): migraine, anxiety, muscle/gym, exercise,
cold/heat (cold water, ice baths, sauna, heat therapy, brown fat), HRV/breathwork (incl. vagus
nerve stimulation), sleep, diet/supplements, mindfulness/yoga, MDMA, cannabis, LSD, psilocybin,
ketamine and other psychedelics.

Lifestyle topics (muscle, exercise, HRV/breathwork, diet, mindfulness) only count when the study
takes healthy volunteers, so a patients-only rehab trial with "exercise" in its title doesn't
show up. Condition topics count for patient trials too.

## Personal profile (optional, kept out of the repo)

`--profile profile.json` personalises the digest. See `profile.example.json`:

- `age`, `sex`: hides studies you can't join (age limits from ClinicalTrials.gov, or parsed from
  ISRCTN's "Who can participate?" text)
- `home`: shows the distance to the nearest site and sorts nearer studies first
- `extra_topics`: more topics, same shape as the built-in ones
- `exclusion_flags`: phrases to look for in each study's exclusion criteria, shown as
  "⚠️ exclusions mention: …" so you can see at a glance what might rule you out

The real profile lives in the private scheduled-task prompt, not here, because this repo is public.

## Digest sections

1. **💷 Healthy volunteers & paid**: posted or updated in the last 7 days
2. **🎯 Your topics**: posted or updated in the last 7 days
3. **🆕 Other new studies**: first posted in the last 7 days
4. **📌 Still open: your topics & paid**: a short standing list of older ones

## Run it

Python 3.9+, standard library only:

```bash
python3 scotland_trials.py --days 7 --profile profile.json --json trials.json --markdown digest.md
python3 -m unittest -v          # offline tests
```

Other options: `--today YYYY-MM-DD`, `--sources ctgov,isrctn`, `--max-other`, `--max-standing`,
`--raw-json` (merged studies with their matching text, for testing a profile offline).

## Weekly Slack digest

Every **Tuesday at 08:35 (UK time)** a Claude scheduled task wakes a dedicated Claude Code session
("Scotland trials · weekly runner", with this repo checked out and Slack + GitHub connected). It:

1. triggers `.github/workflows/scotland-trials.yml` on this branch (GitHub's runners can reach the
   registries; the Claude environment can't), waits for it, and reads the job log;
2. runs `scotland_trials.py --from-ci-log <log> --profile <private profile>` to rebuild the digest
   from the raw data the workflow prints, with the private profile applied locally;
3. corrects each numbered study's snapshot line from `--details`, looks up pay on recruitment
   pages, and searches for paid studies the registries miss;
4. sends the result as a Slack DM.

The workflow also runs the tests and a live scrape (with the example profile) whenever this folder
changes. Its logs only ever contain public registry data and the example profile.

## Getting on studies in Scotland

- Register with [SHARE](https://www.registerforshare.org/), the NHS Research Scotland register that matches people to studies.
- Join the healthy volunteer registers at the [Edinburgh Clinical Research Facility](https://www.ed.ac.uk/clinical-sciences/edinburgh-clinical-research-facility) and similar facilities in Glasgow, Dundee and Aberdeen.
- No commercial phase 1 unit recruiting in Scotland turned up; the big paid healthy-volunteer units found (e.g. Quotient Sciences) are in England.
