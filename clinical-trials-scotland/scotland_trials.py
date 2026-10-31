#!/usr/bin/env python3
"""Find clinical trials and research studies recruiting in Scotland.

Pulls currently-recruiting studies with a Scottish site from two public
registries, works out which were posted or updated in the last N days, tags
them by topic and flags the ones that look paid, then writes a JSON file and a
Slack-ready Markdown digest.

Sources (no API keys needed):
  * ClinicalTrials.gov API v2  (https://clinicaltrials.gov/data-api/api)
  * ISRCTN registry API        (https://www.isrctn.com/page/faqs, "API")

Standard library only, so it runs anywhere with Python 3.9+:

    python3 scotland_trials.py --days 7 --json trials.json --markdown digest.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = (
    "scotland-trials-scraper/1.0 "
    "(+https://github.com/1harryjones1/hen-harrier-tracker)"
)
HTTP_TIMEOUT = 90

# ---------------------------------------------------------------------------
# Topics and payment signals
# ---------------------------------------------------------------------------

# (key, label, regex, scope). "core" scope matches titles, conditions,
# keywords and intervention names only (precise); "all" also searches the
# summary, for drug names that are specific enough not to need guarding.
TOPICS = [
    ("migraine", "Migraine", r"\bmigraine|\bheadache", "core"),
    (
        "muscle",
        "Muscle/gym",
        r"muscle\W+(?:\w+\W+){0,3}?(?:hypertroph|growth|mass|protein synthesis|strength|size)"
        r"|hypertroph\w*\W+(?:\w+\W+){0,3}?muscle|resistance[- ](?:exercise|training)"
        r"|strength training|weight[- ]?(?:lifting|training)|\bcreatine\b|\bwhey\b"
        r"|protein supplement|muscle protein",
        "core",
    ),
    ("mdma", "MDMA", r"\bMDMA\b|methylenedioxymethamphetamine|midomafetamine", "all"),
    (
        "cannabis",
        "Cannabis",
        r"cannabi|\bCBD\b|\bTHC\b|marijuana|tetrahydrocannabinol|nabiximols|sativex"
        r"|dronabinol|nabilone",
        "all",
    ),
    ("lsd", "LSD", r"\bLSD\b|lysergic acid|lysergide|\bMM-?120\b", "all"),
    ("psilocybin", "Psilocybin", r"psilocybin|psilocin|magic mushroom|\bCOMP ?360\b", "all"),
    ("ketamine", "Ketamine", r"ketamine", "all"),
    (
        "psychedelic",
        "Psychedelics",
        r"psychedelic|\bDMT\b|dimethyltryptamine|5-MeO|ayahuasca|mescaline|ibogaine"
        r"|hallucinogen",
        "all",
    ),
    (
        "anxiety",
        "Anxiety",
        r"\banxiety|\banxious|panic disorder|social phobia|generali[sz]ed anxiety|\bGAD\b"
        r"|\bphobia",
        "core",
    ),
]
TOPIC_RES = [(k, label, re.compile(rx, re.I), scope) for k, label, rx, scope in TOPICS]

PAY_RE = re.compile(
    r"£\s?\d[\d,.]*|\b\d+\s?(?:pounds|GBP)\b|\bpaid\b|\bpayments?\b|honorari|voucher"
    r"|gift card|remunerat|stipend|inconvenience (?:allowance|payment)"
    r"|compensat\w*\s+(?:you\s+)?for\s+(?:your\s+)?(?:time|participation|inconvenience)"
    r"|thank[- ]you payment|financial (?:reward|compensation|incentive)",
    re.I,
)
# Matches that are about something else ("paid employment", "payment by results").
PAY_FALSE_POSITIVE_RE = re.compile(
    r"paid (?:employment|work|job|carer|staff)|payments? by results|unpaid|paid attention"
    r"|attention (?:is |will be |must be )?paid|co-?payments?|private(?:ly)? paid"
    r"|pre-?paid|reply-paid|postage|freepost|paid for by|paid by the (?:NHS|sponsor|study)",
    re.I
)
EXPENSES_RE = re.compile(
    r"(?:travel|out[- ]of[- ]pocket|reasonable|parking) (?:costs|expenses)|reimburs", re.I
)

TIER_ORDER = ["paid", "likely_paid", "healthy", "paid_patients", "expenses", "patients"]
TIER_LABEL = {
    "paid": "💷 paid, healthy volunteers",
    "likely_paid": "💷 healthy volunteers, phase 1/industry (often paid)",
    "healthy": "🟡 healthy volunteers (pay not stated)",
    "paid_patients": "💷 paid, but only for people with the condition",
    "expenses": "patients, expenses covered",
    "patients": "patients only",
}
PAYISH_TIERS = {"paid", "likely_paid", "healthy"}  # open to healthy volunteers

# ---------------------------------------------------------------------------
# Scotland geography
# ---------------------------------------------------------------------------

# Coarse polygon (lon, lat) around mainland Scotland and the islands that cuts
# out Northumberland, Cumbria, the Isle of Man and Northern Ireland.
SCOTLAND_POLYGON = [
    (-9.0, 55.27), (-9.0, 61.0), (-0.5, 61.0), (-0.5, 56.0), (-2.03, 55.81),
    (-2.25, 55.64), (-2.20, 55.45), (-2.48, 55.35), (-2.69, 55.19), (-2.86, 55.08),
    (-3.07, 54.99), (-3.30, 54.95), (-3.70, 54.88), (-4.0, 54.75), (-4.4, 54.65),
    (-4.9, 54.55), (-5.3, 54.55), (-5.3, 55.27),
]

SCOTTISH_PLACES = {
    "aberdeen", "airdrie", "alloa", "arbroath", "ayr", "bathgate", "bearsden",
    "bellshill", "broxburn", "cambuslang", "clydebank", "coatbridge", "crosshouse",
    "cumbernauld", "dalkeith", "dingwall", "dumbarton", "dumfries", "dundee",
    "dunfermline", "east kilbride", "edinburgh", "elgin", "falkirk", "forfar",
    "fort william", "galashiels", "glasgow", "glenrothes", "greenock", "hamilton",
    "inverness", "irvine", "kilmarnock", "kirkcaldy", "kirkwall", "lanark", "larbert",
    "lerwick", "livingston", "melrose", "midlothian", "motherwell", "musselburgh",
    "oban", "paisley", "penicuik", "perth", "peterhead", "renfrew", "roslin",
    "rutherglen", "st andrews", "st. andrews", "stirling", "stornoway", "wishaw",
}
SCOTLAND_TEXT_RE = re.compile(
    r"\bscotland\b|\bscottish\b|NHS (?:Lothian|Greater Glasgow|Tayside|Grampian|Highland"
    r"|Fife|Lanarkshire|Ayrshire|Forth Valley|Borders|Dumfries|Shetland|Orkney"
    r"|Western Isles)",
    re.I,
)
UK_COUNTRIES = {"united kingdom", "uk", "great britain", "scotland"}


def point_in_polygon(lon: float, lat: float, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def place_is_scottish(city: str | None, state: str | None, facility: str | None) -> bool:
    city_l = (city or "").strip().lower()
    if city_l in SCOTTISH_PLACES:
        return True
    return bool(SCOTLAND_TEXT_RE.search(" ".join(filter(None, [state, facility, city]))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def http_get(url: str, params: dict | None = None, retries: int = 3) -> bytes:
    if params:
        url = url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last_error = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_error = e
        time.sleep(2 ** (attempt + 1))
    assert last_error is not None
    raise last_error


def parse_date(value: str | None) -> dt.date | None:
    """Accepts 2026-10-30, 2026-10, 2026-10-30T16:04:48Z and similar."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", value.strip())
    if not m:
        return None
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))


def clean(text: str | None, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def pay_signals(text: str) -> tuple[list[str], bool]:
    """Returns (payment evidence snippets, expenses_mentioned)."""
    evidence: list[str] = []
    expenses = bool(EXPENSES_RE.search(text))
    for m in PAY_RE.finditer(text):
        start, end = max(0, m.start() - 70), min(len(text), m.end() + 70)
        window = text[start:end]
        if PAY_FALSE_POSITIVE_RE.search(window):
            continue
        # "travel expenses will be paid" is expenses, not payment.
        if re.search(r"expenses|reimburs", text[max(0, m.start() - 45): m.end() + 25], re.I):
            expenses = True
            continue
        snippet = clean(window)
        if not any(snippet in e or e in snippet for e in evidence):
            evidence.append(("…" if start else "") + snippet + ("…" if end < len(text) else ""))
        if len(evidence) >= 2:
            break
    return evidence, expenses


def match_topics(core_text: str, all_text: str) -> list[str]:
    found = []
    for key, _label, rx, scope in TOPIC_RES:
        if rx.search(core_text) or (scope == "all" and rx.search(all_text)):
            found.append(key)
    return found


PHASE1_RE = re.compile(r"PHASE ?1\b|PHASE I\b|PHASE I/", re.I)


def is_phase1(phases: list[str]) -> bool:
    return any(PHASE1_RE.search(ph.replace("_", " ")) for ph in phases)


def phase_label(phases: list[str]) -> str:
    out = []
    for ph in phases:
        ph = ph.replace("EARLY_PHASE", "early phase ").replace("PHASE", "phase ")
        out.append(re.sub(r"\s+", " ", ph).strip().capitalize())
    return "/".join(out)


def age_label(min_age: str | None, max_age: str | None) -> str:
    """'18 Years', '65 Years' -> '18–65'; only returned when it narrows things."""
    def years(v):
        m = re.match(r"\s*(\d+)\s*(year|yr)?", v or "", re.I)
        if not m:
            return None
        n = int(m.group(1))
        return n if m.group(2) or not re.search(r"month|week|day", v or "", re.I) else 0
    lo, hi = years(min_age), years(max_age)
    if lo and lo > 18 and hi:
        return f"ages {lo}–{hi}"
    if lo and lo > 18:
        return f"ages {lo}+"
    if hi and hi < 100:
        return f"ages {lo or 18}–{hi}" if (lo or 0) >= 16 else f"up to age {hi}"
    return ""


def assign_tier(item: dict, pay_evidence: list[str], expenses: bool) -> str:
    if item.get("healthy_volunteers"):
        if pay_evidence:
            return "paid"
        if is_phase1(item.get("phases", [])) or item.get("sponsor_class") == "INDUSTRY":
            return "likely_paid"
        return "healthy"
    if pay_evidence:
        return "paid_patients"
    if expenses:
        return "expenses"
    return "patients"


# ---------------------------------------------------------------------------
# ClinicalTrials.gov
# ---------------------------------------------------------------------------

CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_FIELDS = ",".join(
    "protocolSection." + m
    for m in [
        "identificationModule", "statusModule", "sponsorCollaboratorsModule",
        "descriptionModule", "conditionsModule", "designModule",
        "armsInterventionsModule", "eligibilityModule", "contactsLocationsModule",
    ]
)
OPEN_SITE_STATUSES = {"", "RECRUITING", "NOT_YET_RECRUITING", "AVAILABLE"}


def ctgov_query(params: dict) -> list[dict]:
    base = {
        "filter.overallStatus": "RECRUITING,NOT_YET_RECRUITING",
        "pageSize": "1000",
        "countTotal": "true",
        "fields": CTGOV_FIELDS,
    }
    base.update(params)
    studies: list[dict] = []
    token = None
    for _ in range(20):  # hard page cap
        p = dict(base)
        if token:
            p["pageToken"] = token
        try:
            data = json.loads(http_get(CTGOV_URL, p))
        except urllib.error.HTTPError as e:
            if e.code == 400 and "fields" in base:
                base.pop("fields")  # field list rejected; fall back to full records
                continue
            raise
        studies.extend(data.get("studies", []))
        if "totalCount" in data:
            print(f"[ctgov] {data['totalCount']} studies for {params}", file=sys.stderr)
        base["countTotal"] = "false"
        token = data.get("nextPageToken")
        if not token:
            break
    return studies


def ctgov_fetch() -> list[dict]:
    seen: dict[str, dict] = {}
    # A circle over Scotland (also catches N. England / N. Ireland, filtered
    # out below) plus a text search that reaches Orkney and Shetland.
    for params in ({"filter.geo": "distance(56.8,-4.2,170mi)"}, {"query.locn": "Scotland"}):
        for s in ctgov_query(params):
            nct = s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            if nct:
                seen[nct] = s
    items = []
    for s in seen.values():
        item = ctgov_normalise(s)
        if item:
            items.append(item)
    return items


def ctgov_normalise(study: dict) -> dict | None:
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    sponsor = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    desc = ps.get("descriptionModule", {})
    cond = ps.get("conditionsModule", {})
    design = ps.get("designModule", {})
    arms = ps.get("armsInterventionsModule", {})
    elig = ps.get("eligibilityModule", {})
    contacts = ps.get("contactsLocationsModule", {})

    sites = []
    for loc in contacts.get("locations", []) or []:
        if (loc.get("country") or "").strip().lower() not in UK_COUNTRIES:
            continue
        gp = loc.get("geoPoint") or {}
        in_scot = place_is_scottish(loc.get("city"), loc.get("state"), loc.get("facility"))
        if not in_scot and "lat" in gp and "lon" in gp:
            in_scot = point_in_polygon(gp["lon"], gp["lat"], SCOTLAND_POLYGON)
        if not in_scot:
            continue
        sites.append({
            "facility": loc.get("facility") or "",
            "city": loc.get("city") or "",
            "status": loc.get("status") or "",
            "contacts": [
                {k: c.get(k) for k in ("name", "email", "phone") if c.get(k)}
                for c in loc.get("contacts", []) or []
            ],
        })
    open_sites = [x for x in sites if x["status"].upper() in OPEN_SITE_STATUSES]
    if not open_sites:
        return None

    std_ages = elig.get("stdAges") or []
    if std_ages and "ADULT" not in std_ages and "OLDER_ADULT" not in std_ages:
        return None  # children only

    nct = ident.get("nctId")
    interventions = [
        clean(f"{i.get('name', '')}") for i in arms.get("interventions", []) or []
    ]
    title = ident.get("briefTitle") or ident.get("officialTitle") or nct
    core_text = " | ".join(
        [title, ident.get("officialTitle") or "", ident.get("acronym") or ""]
        + (cond.get("conditions") or []) + (cond.get("keywords") or []) + interventions
    )
    summary = desc.get("briefSummary") or ""
    all_text = core_text + " | " + summary
    pay_text = " ".join([summary, desc.get("detailedDescription") or "",
                         elig.get("eligibilityCriteria") or ""])

    contact = {}
    for c in (open_sites[0]["contacts"] or []) + [
        {k: c.get(k) for k in ("name", "email", "phone") if c.get(k)}
        for c in contacts.get("centralContacts", []) or []
    ]:
        if c.get("email") or c.get("phone"):
            contact = c
            break

    secondary_ids = [
        x.get("id", "") for x in ident.get("secondaryIdInfos", []) or []
    ]
    item = {
        "source": "ClinicalTrials.gov",
        "id": nct,
        "aliases": [s.upper().replace(" ", "") for s in secondary_ids if "ISRCTN" in s.upper()],
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "title": clean(title),
        "status": status.get("overallStatus", ""),
        "first_posted": (status.get("studyFirstPostDateStruct") or {}).get("date"),
        "last_updated": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "sites": [{"facility": x["facility"], "city": x["city"], "status": x["status"]}
                  for x in open_sites],
        "healthy_volunteers": bool(elig.get("healthyVolunteers")),
        "phases": design.get("phases") or [],
        "study_type": design.get("studyType", ""),
        "sponsor": sponsor.get("name", ""),
        "sponsor_class": sponsor.get("class", ""),
        "conditions": cond.get("conditions") or [],
        "interventions": [i for i in interventions if i],
        "sex": elig.get("sex", ""),
        "ages": age_label(elig.get("minimumAge"), elig.get("maximumAge")),
        "summary": clean(summary, 400),
        "contact": contact,
        "topics": match_topics(core_text, all_text),
    }
    evidence, expenses = pay_signals(pay_text)
    item["pay_evidence"] = evidence
    item["tier"] = assign_tier(item, evidence, expenses)
    return item


# ---------------------------------------------------------------------------
# ISRCTN
# ---------------------------------------------------------------------------

ISRCTN_URL = "https://www.isrctn.com/api/query/format/default"
CLOSED_OVERRIDE_RE = re.compile(r"stop|suspend|terminat|withdraw|complet|closed|ended", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, path: str):
    """Namespace-agnostic find: path like 'trialDescription/title'."""
    cur = [el]
    for part in path.split("/"):
        nxt = []
        for c in cur:
            nxt.extend(ch for ch in c if _local(ch.tag) == part)
        cur = nxt
        if not cur:
            return []
    return cur


def _text(el, path: str) -> str:
    found = _find(el, path)
    return (found[0].text or "").strip() if found else ""


def _iter_local(el, name: str):
    for x in el.iter():
        if _local(x.tag) == name:
            yield x


def isrctn_query(q: str, limit: int) -> list:
    raw = http_get(ISRCTN_URL, {"q": q, "limit": str(limit)})
    root = ET.fromstring(raw)
    trials = [t for t in _iter_local(root, "trial")]
    total = root.get("totalCount")
    print(f"[isrctn] {len(trials)} of {total} records for: {q}", file=sys.stderr)
    if total and total.isdigit() and int(total) > len(trials):
        print("[isrctn] warning: results truncated, raise the limit", file=sys.stderr)
    return trials


def isrctn_fetch(today: dt.date) -> list[dict]:
    day = today.isoformat() + "T00:00:00"
    queries = [
        # Every trial that lists Scotland as a recruitment country and has not
        # yet ended overall.
        (f"recruitmentCountry:Scotland AND overallEndDate GE {day}", 2000),
        # Trials that list only "United Kingdom" but have Scottish centres.
        (
            'recruitmentCountry:"United Kingdom" AND overallEndDate GE ' + day
            + " AND (Edinburgh OR Glasgow OR Aberdeen OR Dundee OR Inverness OR Stirling)",
            1000,
        ),
    ]
    seen: dict[str, dict] = {}
    for q, limit in queries:
        for trial in isrctn_query(q, limit):
            item = isrctn_normalise(trial, today)
            if item:
                seen[item["id"]] = item
    return list(seen.values())


def isrctn_status(trial, today: dt.date) -> str:
    override = _text(trial, "participants/recruitmentStatusOverride")
    if override and CLOSED_OVERRIDE_RE.search(override):
        return "CLOSED"
    start = parse_date(_text(trial, "participants/recruitmentStart"))
    end = parse_date(_text(trial, "participants/recruitmentEnd"))
    if end and end < today:
        return "CLOSED"
    if start and start > today:
        return "NOT_YET_RECRUITING"
    return "RECRUITING"


def isrctn_normalise(trial, today: dt.date) -> dict | None:
    status = isrctn_status(trial, today)
    if status == "CLOSED":
        return None
    isrctn_el = _find(trial, "isrctn")
    number = (isrctn_el[0].text or "").strip() if isrctn_el else ""
    if not number:
        return None
    ident = number if number.upper().startswith("ISRCTN") else f"ISRCTN{number}"

    sites = []
    for centre in _find(trial, "participants/trialCentres/trialCentre"):
        country = _text(centre, "country")
        city = _text(centre, "city")
        name = _text(centre, "name")
        if country.lower() not in UK_COUNTRIES:
            continue
        if country.lower() == "scotland" or place_is_scottish(city, _text(centre, "state"), name):
            sites.append({"facility": name, "city": city, "status": ""})
    if not sites:
        return None

    ages = _text(trial, "participants/ageRange")
    if ages and re.fullmatch(r"(?i)\s*(child|neonate|neonatal|infant)s?\s*", ages):
        return None

    title = _text(trial, "trialDescription/title")
    sci_title = _text(trial, "trialDescription/scientificTitle")
    acronym = _text(trial, "trialDescription/acronym")
    lay = _text(trial, "trialDescription/plainEnglishSummary")
    conditions = [
        _text(c, "description") for c in _find(trial, "conditions/condition")
    ]
    interventions = [
        _text(i, "description") for i in _find(trial, "interventions/intervention")
    ]
    drug_names = [x.text or "" for x in _iter_local(trial, "drugNames")]
    phases = sorted({(x.text or "").strip() for x in _iter_local(trial, "phase")
                     if (x.text or "").strip() and "not applicable" not in (x.text or "").lower()})
    participant_types = [(x.text or "").strip() for x in _iter_local(trial, "participantType")]
    hv_flag = _text(trial, "participants/healthyVolunteersAllowed").lower() == "true"
    healthy = hv_flag or any("healthy" in p.lower() for p in participant_types)

    sponsor_name, sponsor_type = "", ""
    for sp in _iter_local(trial, "sponsor"):
        sponsor_name = sponsor_name or next(
            (x.text.strip() for x in _iter_local(sp, "organisation") if x.text), "")
        sponsor_type = sponsor_type or next(
            (x.text.strip() for x in _iter_local(sp, "sponsorType") if x.text), "")
    if not sponsor_name:
        m = re.search(r"Where is the study run from\?\s*(.+)", lay)
        sponsor_name = clean(m.group(1), 80) if m else ""

    contact = {}
    m = re.search(r"Who is the main contact\?\s*(.+?)(?:\n\s*\n|$)", lay, re.S)
    if m:
        contact_text = clean(m.group(1), 160)
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", contact_text)
        contact = {"name": contact_text}
        if email:
            contact["email"] = email.group(0)

    clinicaltrials_gov = _text(trial, "externalRefs/clinicalTrialsGovNumber")
    core_text = " | ".join([title, sci_title, acronym] + conditions + drug_names)
    all_text = core_text + " | " + lay + " | " + " ".join(interventions)

    summary = lay
    m = re.search(r"Background and study aims\s*(.+?)(?:Who can participate\?|$)", lay, re.S)
    if m:
        summary = m.group(1)

    item = {
        "source": "ISRCTN",
        "id": ident,
        "aliases": [clinicaltrials_gov.upper()] if clinicaltrials_gov.upper().startswith("NCT") else [],
        "url": f"https://www.isrctn.com/{ident}",
        "title": clean(title or sci_title),
        "status": status,
        "first_posted": (isrctn_el[0].get("dateAssigned") or "")[:10] or None,
        "last_updated": (trial.get("lastUpdated") or "")[:10] or None,
        "start_date": _text(trial, "participants/recruitmentStart")[:10] or None,
        "sites": sites,
        "healthy_volunteers": healthy,
        "phases": phases,
        "study_type": _text(trial, "trialDesign/primaryStudyDesign"),
        "sponsor": sponsor_name,
        "sponsor_class": "INDUSTRY" if "industry" in sponsor_type.lower() else sponsor_type,
        "conditions": [c for c in conditions if c],
        "interventions": [clean(i, 120) for i in drug_names + interventions if i][:3],
        "sex": _text(trial, "participants/gender"),
        "ages": "older adults" if ages.lower() == "senior" else "",
        "summary": clean(summary, 400),
        "contact": contact,
        "topics": match_topics(core_text, all_text),
    }
    evidence, expenses = pay_signals(lay + " " + _text(trial, "participants/inclusion"))
    item["pay_evidence"] = evidence
    item["tier"] = assign_tier(item, evidence, expenses)
    return item


# ---------------------------------------------------------------------------
# Combine, classify, render
# ---------------------------------------------------------------------------


def merge_duplicates(items: list[dict]) -> list[dict]:
    """A trial on both registries is kept once (ISRCTN record preferred)."""
    by_id = {it["id"].upper(): it for it in items}
    drop = set()
    for it in items:
        if it["source"] != "ISRCTN":
            continue
        for alias in it.get("aliases", []):
            other = by_id.get(alias)
            if other and other is not it:
                drop.add(alias)
                it["also"] = other["url"]
                it["healthy_volunteers"] = it["healthy_volunteers"] or other["healthy_volunteers"]
                it["topics"] = sorted(set(it["topics"]) | set(other["topics"]),
                                      key=[t[0] for t in TOPICS].index)
                if TIER_ORDER.index(other["tier"]) < TIER_ORDER.index(it["tier"]):
                    it["tier"], it["pay_evidence"] = other["tier"], other["pay_evidence"]
    return [it for it in items if it["id"].upper() not in drop]


def classify(items: list[dict], since: dt.date) -> None:
    for it in items:
        first = parse_date(it.get("first_posted"))
        last = parse_date(it.get("last_updated"))
        it["new"] = bool(first and first >= since)
        it["updated"] = bool(not it["new"] and last and last >= since)


def sort_key(it: dict):
    """Payment tier first, then new before updated, then most recent first."""
    date = parse_date(it.get("first_posted") if it.get("new") else it.get("last_updated"))
    return (TIER_ORDER.index(it["tier"]), not it.get("new"),
            -(date or dt.date.min).toordinal())


def short_date(value: str | None) -> str:
    d = parse_date(value)
    return f"{d.day} {d.strftime('%b')}" if d else "?"


TOPIC_LABELS = {k: label for k, label, _, _ in TOPICS}


def describe(it: dict, number: int) -> str:
    """One numbered study, Slack-safe (bold number avoids list renumbering)."""
    cities = sorted({s["city"] for s in it["sites"] if s["city"]})
    bits = [", ".join(cities[:3]) or "Scotland"]
    if it["phases"]:
        bits.append(phase_label(it["phases"]))
    if it["topics"]:
        bits.append(", ".join(TOPIC_LABELS[t] for t in it["topics"]))
    bits.append(TIER_LABEL[it["tier"]])
    if it.get("ages"):
        bits.append(it["ages"])
    if (it.get("sex") or "").upper() in {"FEMALE", "MALE"}:
        bits.append(f"{it['sex'].lower()} only")
    if it["status"] == "NOT_YET_RECRUITING":
        bits.append("opening soon")
    if it.get("new"):
        bits.append(f"new {short_date(it['first_posted'])}")
    elif it.get("updated"):
        bits.append(f"updated {short_date(it['last_updated'])}")
    line = (f"**{number}. {clean(it['title'], 95)}**\n"
            f"{' · '.join(bits)} · [{it['id']}]({it['url']})")
    if it["pay_evidence"]:
        line += f"\n_“{clean(it['pay_evidence'][0], 150)}”_"
    return line


def describe_short(it: dict) -> str:
    cities = sorted({s["city"] for s in it["sites"] if s["city"]})
    tags = [TOPIC_LABELS[t] for t in it["topics"]]
    if it["tier"] in {"paid", "likely_paid"}:
        tags.append("💷")
    return (f"• {clean(it['title'], 80)} · {', '.join(cities[:2]) or 'Scotland'}"
            f"{' · ' + ', '.join(tags) if tags else ''} · [{it['id']}]({it['url']})")


def build_sections(items: list[dict]) -> dict[str, list[dict]]:
    week = [it for it in items if it["new"] or it["updated"]]
    payish = sorted([it for it in week if it["tier"] in PAYISH_TIERS], key=sort_key)
    topical = sorted([it for it in week if it["topics"] and it["tier"] not in PAYISH_TIERS],
                     key=sort_key)
    shown = {it["id"] for it in payish + topical}
    other_new = sorted([it for it in items if it["new"] and it["id"] not in shown],
                       key=sort_key)
    standing = sorted(
        [it for it in items if not (it["new"] or it["updated"])
         and (it["topics"] or it["tier"] in {"paid", "likely_paid"})],
        key=lambda it: (not it["topics"], sort_key(it)),
    )
    return {"payish": payish, "topical": topical, "other_new": other_new,
            "standing": standing, "week": week}


def render_markdown(items: list[dict], sources: dict, since: dt.date, today: dt.date,
                    max_other: int = 6, max_standing: int = 8, max_chars: int = 4800) -> str:
    """Slack digest. Shrinks the lower sections until it fits in one message."""
    sec = build_sections(items)
    caps = {"payish": 10, "topical": 10, "other_new": max_other, "standing": max_standing}

    def render() -> str:
        out = [
            f"**🧪 Scotland trials · week to {today.strftime('%a')} {today.day} "
            f"{today.strftime('%b')}**",
            f"_{sum(i['new'] for i in items)} new and {sum(i['updated'] for i in items)} "
            f"updated studies recruiting in Scotland since {short_date(since.isoformat())} · "
            f"{len(sec['payish'])} for healthy volunteers or paid · "
            f"{sum(bool(i['topics']) for i in sec['week'])} on your topics_",
            "",
        ]
        n = 0
        groups = [
            ("payish", "**💷 HEALTHY VOLUNTEERS & PAID**", "_Nothing new this week._"),
            ("topical", "**🎯 YOUR TOPICS**",
             "_Nothing new on migraine, muscle, MDMA, cannabis, LSD, psilocybin, ketamine "
             "or anxiety this week._"),
            ("other_new", "**🆕 OTHER NEW STUDIES**", None),
        ]
        for key, heading, empty in groups:
            rows, cap = sec[key], caps[key]
            if not rows and empty is None:
                continue
            more = f" ({min(len(rows), cap)} of {len(rows)})" if len(rows) > cap else ""
            out.append(heading + more)
            if not rows:
                out.append(empty)
            for it in rows[:cap]:
                n += 1
                out.append(describe(it, n))
            out.append("")
        if sec["standing"] and caps["standing"]:
            rows = sec["standing"]
            more = (f" ({min(len(rows), caps['standing'])} of {len(rows)})"
                    if len(rows) > caps["standing"] else "")
            out.append("**📌 STILL OPEN: YOUR TOPICS & PAID**" + more)
            out.extend(describe_short(it) for it in rows[:caps["standing"]])
            out.append("")
        bits = [f"{name} ✓ {info['count']}" if info["ok"] else f"{name} ✗ ({info['error']})"
                for name, info in sources.items()]
        out.append("_Sources: " + " · ".join(bits) + "_")
        return "\n".join(out).strip() + "\n"

    text = render()
    for key in ("standing", "other_new", "topical", "payish"):
        while len(text) > max_chars and caps[key] > (0 if key in ("standing", "other_new") else 3):
            caps[key] -= 1
            text = render()
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7, help="look-back window (default 7)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    ap.add_argument("--sources", default="ctgov,isrctn", help="comma list: ctgov,isrctn")
    ap.add_argument("--json", help="write all matching studies as JSON here")
    ap.add_argument("--markdown", help="write the Slack-ready digest here")
    ap.add_argument("--max-other", type=int, default=6)
    ap.add_argument("--max-standing", type=int, default=8)
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    since = today - dt.timedelta(days=args.days)
    wanted = {s.strip() for s in args.sources.split(",") if s.strip()}

    fetchers = {
        "ctgov": ("ClinicalTrials.gov", ctgov_fetch),
        "isrctn": ("ISRCTN", lambda: isrctn_fetch(today)),
    }
    items: list[dict] = []
    sources: dict[str, dict] = {}
    for key, (name, fn) in fetchers.items():
        if key not in wanted:
            continue
        try:
            got = fn()
            items.extend(got)
            sources[name] = {"ok": True, "count": len(got), "error": None}
        except Exception as e:  # keep going with whatever else works
            sources[name] = {"ok": False, "count": 0, "error": f"{type(e).__name__}: {e}"[:200]}
            print(f"[warn] {name} failed: {e}", file=sys.stderr)

    items = merge_duplicates(items)
    classify(items, since)
    items.sort(key=sort_key)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "since": since.isoformat(),
        "sources": sources,
        "counts": {
            "recruiting_in_scotland": len(items),
            "new": sum(i["new"] for i in items),
            "updated": sum(i["updated"] for i in items),
            "payish_this_week": sum((i["new"] or i["updated"]) and i["tier"] in PAYISH_TIERS
                                    for i in items),
            "topics_this_week": sum(bool((i["new"] or i["updated"]) and i["topics"])
                                    for i in items),
        },
        "studies": items,
    }
    md = render_markdown(items, sources, since, today, args.max_other, args.max_standing)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md)
    if not args.json and not args.markdown:
        sys.stdout.write(md)
    print(json.dumps({"sources": sources, "counts": payload["counts"]}), file=sys.stderr)
    return 0 if any(s["ok"] for s in sources.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
