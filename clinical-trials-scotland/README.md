# Scotland clinical trials scraper

Finds clinical trials and research studies that are **recruiting in Scotland right now**,
flags the ones open to healthy volunteers or that mention payment, tags the topics I care
about, and writes a Slack-ready weekly digest.

It has nothing to do with the hen harrier tracker; it just lives in this repo.

## What it checks

| Source | How | Notes |
| --- | --- | --- |
| [ClinicalTrials.gov](https://clinicaltrials.gov/data-api/api) | API v2, recruiting / not-yet-recruiting studies inside a circle over Scotland plus a "Scotland" location search | Sites are kept only if they fall inside a Scotland polygon or name a Scottish town or health board, and the Scottish site itself is still open |
| [ISRCTN](https://www.isrctn.com/) | Public XML API, `recruitmentCountry:Scotland` (and UK-only records with Scottish centres) whose end date hasn't passed | Recruitment status is worked out from the recruitment dates; the plain-English summary often states payment |

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

Payment detection is keyword-based, so each 💷 line quotes the sentence it matched. Read it before you get excited.

Topics: migraine, muscle/gym (hypertrophy, resistance training, protein, creatine; heart "hypertrophy" is ignored), MDMA, cannabis/CBD, LSD, psilocybin, ketamine, other psychedelics, anxiety. Edit `TOPICS` in `scotland_trials.py` to change them.

## Digest sections

1. **💷 Healthy volunteers & paid**: posted or updated in the last 7 days
2. **🎯 Your topics**: posted or updated in the last 7 days
3. **🆕 Other new studies**: first posted in the last 7 days
4. **📌 Still open: your topics & paid**: a short standing list of older ones

## Run it

Python 3.9+, standard library only:

```bash
python3 scotland_trials.py --days 7 --json trials.json --markdown digest.md
python3 -m unittest -v          # offline tests
```

Options: `--days N`, `--today YYYY-MM-DD`, `--sources ctgov,isrctn`, `--max-other`, `--max-standing`.

## Weekly Slack digest

A Claude scheduled task runs every **Tuesday at 08:35 (UK time)**: it downloads this script from GitHub, runs it, checks the 💷 flags, does a few web searches for paid studies the registries miss (university "volunteers needed" posts and the like), and DMs the result to me on Slack.

`.github/workflows/scotland-trials.yml` runs the tests and a live scrape whenever this folder changes, and uploads `trials.json` and `digest.md` as a build artifact.

## Tips for actually getting on paid studies in Scotland

- Register with [SHARE](https://www.registerforshare.org/), the NHS Research Scotland register that matches you to studies.
- Join the healthy volunteer registers at the [Edinburgh Clinical Research Facility](https://www.ed.ac.uk/clinical-sciences/edinburgh-clinical-research-facility) and similar facilities in Glasgow, Dundee and Aberdeen.
- I couldn't find a commercial phase 1 unit recruiting in Scotland; the big paid healthy-volunteer units I found (e.g. Quotient Sciences) are in England.
