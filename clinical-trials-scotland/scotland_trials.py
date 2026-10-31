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

An optional --profile JSON file personalises it (kept out of this repo):

    {
      "age": 30, "sex": "male",
      "home": {"name": "Perth", "lat": 56.39, "lon": -3.43},
      "extra_topics": [{"key": "tinnitus", "label": "Tinnitus",
                        "regex": "tinnitus", "scope": "core", "hv_only": false}],
      "exclusion_flags": [{"label": "regular meds",
                           "regex": "regular medication|prescription medication"}]
    }

age/sex hide studies you can't join, home adds distances and sorts nearer
studies first, extra_topics are added to the built-in topics, and
exclusion_flags are searched for in each study's exclusion criteria.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
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

# Built-in topics. "core" scope matches titles, conditions, keywords and
# intervention names only (precise); "all" also searches the summary, for terms
# specific enough not to need guarding. hv_only topics (lifestyle ones) only
# count when the study takes healthy volunteers: a patients-only rehab trial
# with "exercise" in the title isn't something you can sign up for.
DEFAULT_TOPICS = [
    {"key": "migraine", "label": "Migraine", "regex": r"\bmigraine|\bheadache", "scope": "core"},
    {"key": "anxiety", "label": "Anxiety", "scope": "core",
     "regex": r"\banxiety|\banxious|panic disorder|social phobia|generali[sz]ed anxiety|\bGAD\b"
              r"|\bphobia"},
    {"key": "muscle", "label": "Muscle/gym", "scope": "core", "hv_only": True,
     "regex": r"muscle\W+(?:\w+\W+){0,3}?(?:hypertroph|growth|mass|protein synthesis|strength|size)"
              r"|hypertroph\w*\W+(?:\w+\W+){0,3}?muscle|resistance[- ](?:exercise|training)"
              r"|strength training|weight[- ]?(?:lifting|training)|\bcreatine\b|\bwhey\b"
              r"|protein supplement|muscle protein"},
    {"key": "exercise", "label": "Exercise", "scope": "core", "hv_only": True,
     "regex": r"\bexercis|physical activity|interval training|\bHIIT\b|aerobic|\bVO2|zone 2"
              r"|cardiorespiratory|endurance|\bcycling\b|\brunning\b"},
    {"key": "cold_heat", "label": "Cold/heat", "scope": "core",
     "regex": r"cold[- ](?:water|exposure|immersion|acclimati|shower|plunge|stress)|ice[- ]bath"
              r"|cryotherap|\bsauna|heat (?:therapy|exposure|acclimati|stress|stimulus)"
              r"|hot[- ]water immersion|passive heating|thermoregulat|thermogenesis"
              r"|brown (?:adipose|fat)"},
    {"key": "hrv_breath", "label": "HRV/breathwork", "scope": "core", "hv_only": True,
     "regex": r"heart rate variability|\bHRV\b|breath(?:ing|work)\W+(?:\w+\W+){0,2}?"
              r"(?:training|exercise|technique|intervention|practice)|slow[- ]paced breathing"
              r"|resonance (?:frequency )?breathing|paced breathing|biofeedback"
              r"|vag(?:us|al) nerve stimulation|\b[tn]VNS\b|wim hof"},
    {"key": "sleep", "label": "Sleep", "scope": "core",
     "regex": r"\bsleep|insomnia|circadian|melatonin|light therapy|bright light|chronotype"},
    {"key": "nutrition", "label": "Diet/supplements", "scope": "core", "hv_only": True,
     "regex": r"supplement|\bprotein\b|creatine|caffeine|magnesium|riboflavin|coenzyme Q10"
              r"|\bCoQ10\b|citicoline|theanine|tyrosine|probiotic|prebiotic|\bfib(?:re|er)\b"
              r"|\bdiet|fasting|time-restricted|\bmeals?\b|appetite|gut microbio"},
    {"key": "mind", "label": "Mindfulness/yoga", "scope": "core", "hv_only": True,
     "regex": r"mindfulness|meditation|\byoga\b|\bNSDR\b|non-sleep deep rest"
              r"|relaxation training|tai chi|qigong"},
    {"key": "mdma", "label": "MDMA", "scope": "all",
     "regex": r"\bMDMA\b|methylenedioxymethamphetamine|midomafetamine"},
    {"key": "cannabis", "label": "Cannabis", "scope": "all",
     "regex": r"cannabi|\bCBD\b|\bTHC\b|marijuana|tetrahydrocannabinol|nabiximols|sativex"
              r"|dronabinol|nabilone"},
    {"key": "lsd", "label": "LSD", "scope": "all",
     "regex": r"\bLSD\b|lysergic acid|lysergide|\bMM-?120\b",
     # "LSD" is also lysosomal storage disease
     "exclude": r"lysosomal"},
    {"key": "psilocybin", "label": "Psilocybin", "scope": "all",
     "regex": r"psilocybin|psilocin|magic mushroom|\bCOMP ?360\b"},
    {"key": "ketamine", "label": "Ketamine", "regex": r"ketamine", "scope": "all"},
    {"key": "psychedelic", "label": "Psychedelics", "scope": "all",
     "regex": r"psychedelic|\bDMT\b|dimethyltryptamine|5-MeO|ayahuasca|mescaline|ibogaine"
              r"|hallucinogen"},
]
# Exclusions that apply whatever the topic list.
TOPIC_GLOBAL_EXCLUDE = {"sleep": r"apn(?:o)?ea|narcolep"}


def compile_topics(topics: list[dict]) -> list[dict]:
    out = []
    for t in topics:
        c = dict(t)
        c["rx"] = re.compile(t["regex"], re.I)
        excl = t.get("exclude") or TOPIC_GLOBAL_EXCLUDE.get(t["key"])
        c["exclude_rx"] = re.compile(excl, re.I) if excl else None
        out.append(c)
    return out


TOPICS = compile_topics(DEFAULT_TOPICS)

PAY_RE = re.compile(
    r"£\s?\d[\d,.]*|\b\d+\s?GBP\b|\bpaid\b|\bpayments?\b|honorari|voucher"
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

# Approximate (lat, lon) for towns that host trial sites, used to recognise
# Scottish sites and to estimate distance from home when a registry gives a
# town but no coordinates (ISRCTN).
CITY_COORDS = {
    "aberdeen": (57.15, -2.09), "airdrie": (55.87, -3.98), "alloa": (56.12, -3.79),
    "arbroath": (56.56, -2.58), "ayr": (55.46, -4.63), "bathgate": (55.90, -3.64),
    "bearsden": (55.92, -4.33), "bellshill": (55.82, -4.02), "broxburn": (55.93, -3.47),
    "cambuslang": (55.81, -4.16), "clydebank": (55.90, -4.40), "coatbridge": (55.86, -4.02),
    "crosshouse": (55.62, -4.55), "cumbernauld": (55.95, -3.99), "dalkeith": (55.89, -3.07),
    "dingwall": (57.60, -4.43), "dumbarton": (55.94, -4.57), "dumfries": (55.07, -3.60),
    "dundee": (56.46, -2.97), "dunfermline": (56.07, -3.45), "east kilbride": (55.76, -4.18),
    "edinburgh": (55.95, -3.19), "elgin": (57.65, -3.32), "falkirk": (56.00, -3.78),
    "forfar": (56.64, -2.89), "fort william": (56.82, -5.11), "galashiels": (55.62, -2.81),
    "glasgow": (55.86, -4.25), "glenrothes": (56.20, -3.18), "greenock": (55.95, -4.76),
    "hamilton": (55.78, -4.04), "inverness": (57.48, -4.22), "irvine": (55.61, -4.67),
    "kilmarnock": (55.61, -4.50), "kirkcaldy": (56.11, -3.16), "kirkwall": (58.98, -2.96),
    "lanark": (55.67, -3.78), "larbert": (56.02, -3.83), "lerwick": (60.15, -1.14),
    "livingston": (55.90, -3.52), "lothian": (55.95, -3.19), "melrose": (55.60, -2.72),
    "midlothian": (55.89, -3.07), "motherwell": (55.79, -3.99), "musselburgh": (55.94, -3.05),
    "oban": (56.41, -5.47), "paisley": (55.85, -4.42), "penicuik": (55.83, -3.22),
    "perth": (56.40, -3.43), "peterhead": (57.51, -1.78), "renfrew": (55.87, -4.39),
    "roslin": (55.86, -3.16), "rutherglen": (55.83, -4.21), "st andrews": (56.34, -2.80),
    "st. andrews": (56.34, -2.80), "stirling": (56.12, -3.94), "stornoway": (58.21, -6.39),
    "wishaw": (55.77, -3.92),
}
SCOTTISH_PLACES = set(CITY_COORDS)
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


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8  # earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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


def match_topics(core_text: str, all_text: str, healthy: bool = True,
                 topics: list[dict] | None = None) -> list[str]:
    found = []
    for t in topics if topics is not None else TOPICS:
        if t.get("hv_only") and not healthy:
            continue
        if t["exclude_rx"] and t["exclude_rx"].search(core_text + " " + all_text):
            continue
        if t["rx"].search(core_text) or (t.get("scope") == "all" and t["rx"].search(all_text)):
            found.append(t["key"])
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


def years(value: str | None) -> int | None:
    """'18 Years' -> 18, '6 Months' -> 0, '' -> None."""
    m = re.match(r"\s*(\d+)\s*(year|yr)?", value or "", re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n if m.group(2) or not re.search(r"month|week|day", value or "", re.I) else 0


def ages_from_text(text: str) -> tuple[int | None, int | None]:
    """Best-effort ages from lay text such as 'Adults aged 18-65' or 'over 50'."""
    t = text or ""
    m = re.search(r"\b(\d{1,2})\s*(?:-|–|to|and)\s*(\d{2,3})\s*(?:years|yrs|-year|years old)?", t)
    if m and 0 < int(m.group(1)) < int(m.group(2)) <= 110 and re.search(
            r"age|aged|years|yrs|adults?", t[max(0, m.start() - 40): m.end() + 20], re.I):
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?:aged|over|above|older than|at least)\s*(\d{2})\b", t, re.I) or \
        re.search(r"\b(\d{2})\s*(?:years)?\s*(?:and|or)\s*(?:over|older|above)", t, re.I)
    lo = int(m.group(1)) if m else None
    m = re.search(r"(?:under|below|younger than|up to)\s*(?:the age of\s*)?(\d{2})\b", t, re.I)
    hi = int(m.group(1)) if m else None
    if lo is not None and hi is not None and hi <= lo:
        hi = None
    return lo, hi


def age_label(min_age, max_age) -> str:
    """'18 Years', '65 Years' (or ints) -> 'ages 18–65'; only when it narrows things."""
    lo = min_age if isinstance(min_age, int) or min_age is None else years(min_age)
    hi = max_age if isinstance(max_age, int) or max_age is None else years(max_age)
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
            "lat": gp.get("lat"),
            "lon": gp.get("lon"),
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
        "sites": [{k: x[k] for k in ("facility", "city", "status", "lat", "lon")}
                  for x in open_sites],
        "healthy_volunteers": bool(elig.get("healthyVolunteers")),
        "phases": design.get("phases") or [],
        "study_type": design.get("studyType", ""),
        "sponsor": sponsor.get("name", ""),
        "sponsor_class": sponsor.get("class", ""),
        "conditions": cond.get("conditions") or [],
        "interventions": [i for i in interventions if i],
        "sex": elig.get("sex", ""),
        "min_age": years(elig.get("minimumAge")),
        "max_age": years(elig.get("maximumAge")),
        "summary": clean(summary, 400),
        "contact": contact,
        # working text for topic/eligibility matching (not written to JSON)
        "_core": core_text,
        "_all": all_text,
        "_exclusion": exclusion_section(elig.get("eligibilityCriteria") or ""),
    }
    item["ages"] = age_label(item["min_age"], item["max_age"])
    evidence, expenses = pay_signals(pay_text)
    item["pay_evidence"] = evidence
    item["tier"] = assign_tier(item, evidence, expenses)
    item["topics"] = match_topics(core_text, all_text, item["tier"] in PAYISH_TIERS)
    return item


def exclusion_section(criteria: str) -> str:
    """The exclusion part of a ClinicalTrials.gov eligibility block (or all of it)."""
    m = re.search(r"exclusion criteria\s*:?", criteria, re.I)
    return criteria[m.end():] if m else criteria


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
            lat, lon = CITY_COORDS.get(city.strip().lower(), (None, None))
            sites.append({"facility": name, "city": city, "status": "", "lat": lat, "lon": lon})
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

    m = re.search(r"Who can participate\?\s*(.+?)(?:What does the study involve\?|$)", lay, re.S)
    min_age, max_age = ages_from_text(m.group(1) if m else "")
    if min_age is None and ages.lower() == "senior":
        min_age = 65

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
        "min_age": min_age,
        "max_age": max_age,
        "summary": clean(summary, 400),
        "contact": contact,
        "_core": core_text,
        "_all": all_text,
        "_exclusion": _text(trial, "participants/exclusion"),
    }
    item["ages"] = age_label(min_age, max_age)
    evidence, expenses = pay_signals(lay + " " + _text(trial, "participants/inclusion"))
    item["pay_evidence"] = evidence
    item["tier"] = assign_tier(item, evidence, expenses)
    item["topics"] = match_topics(core_text, all_text, item["tier"] in PAYISH_TIERS)
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
                for key in ("_core", "_all", "_exclusion"):
                    it[key] = (it.get(key) or "") + " | " + (other.get(key) or "")
                if it.get("min_age") is None and it.get("max_age") is None:
                    it["min_age"], it["max_age"] = other.get("min_age"), other.get("max_age")
                    it["ages"] = other.get("ages", "")
                if TIER_ORDER.index(other["tier"]) < TIER_ORDER.index(it["tier"]):
                    it["tier"], it["pay_evidence"] = other["tier"], other["pay_evidence"]
                it["topics"] = match_topics(it["_core"], it["_all"], it["tier"] in PAYISH_TIERS)
    return [it for it in items if it["id"].upper() not in drop]


# Titles that make a study single-sex even when the registry says "all".
SEX_TITLE_RE = {
    "male": re.compile(
        r"pregnan|breast-?fe|lactat|maternal|postpartum|post-partum|menopaus|endometriosis"
        r"|(?<!men and )(?<!men & )\bwomen\b|\bmothers?\b|ovari|\bovary|uterine|uterus"
        r"|intrauterine|cervical (?:cancer|screening)|contracepti|\bPCOS\b|gynae|gyne", re.I),
    "female": re.compile(
        r"prostat|(?<!women and )(?<!women & )\bmen\b(?! and women)|testic|erectile"
        r"|\bfathers?\b", re.I),
}


def load_profile(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    for t in profile.get("extra_topics", []):
        if not {"key", "label", "regex"} <= set(t):
            raise ValueError(f"extra topic needs key, label and regex: {t}")
        re.compile(t["regex"])
    for flag in profile.get("exclusion_flags", []):
        re.compile(flag["regex"])
    return profile


def apply_profile(items: list[dict], profile: dict) -> list[dict]:
    """Re-tags topics with the full topic list, then marks eligibility, exclusion
    flags and distance. Returns the compiled topic list in use."""
    topics = TOPICS
    if profile.get("extra_topics"):
        extra_keys = {t["key"] for t in profile["extra_topics"]}
        topics = compile_topics([t for t in DEFAULT_TOPICS if t["key"] not in extra_keys]
                                + profile["extra_topics"])
    flags = [(f["label"], re.compile(f["regex"], re.I))
             for f in profile.get("exclusion_flags", [])]
    age, sex = profile.get("age"), (profile.get("sex") or "").lower()
    home = profile.get("home") or {}

    for it in items:
        it["topics"] = match_topics(it.get("_core", ""), it.get("_all", ""),
                                    it["tier"] in PAYISH_TIERS, topics)
        reasons = []
        if isinstance(age, int):
            if it.get("min_age") is not None and age < it["min_age"]:
                reasons.append(f"min age {it['min_age']}")
            if it.get("max_age") is not None and age > it["max_age"]:
                reasons.append(f"max age {it['max_age']}")
        study_sex = (it.get("sex") or "").lower()
        if sex in {"male", "female"} and study_sex in {"male", "female"} and study_sex != sex:
            reasons.append(f"{study_sex} only")
        elif sex in SEX_TITLE_RE and SEX_TITLE_RE[sex].search(it.get("title", "")):
            reasons.append("other sex (from title)")
        it["eligible"] = not reasons
        it["ineligible_reason"] = ", ".join(reasons)
        exclusion = it.get("_exclusion") or ""
        it["flags"] = [label for label, rx in flags if rx.search(exclusion)]

        it["miles"], it["nearest"] = None, ""
        if "lat" in home and "lon" in home:
            best = None
            for s in it["sites"]:
                if s.get("lat") is None or s.get("lon") is None:
                    continue
                d = miles_between(home["lat"], home["lon"], s["lat"], s["lon"])
                if best is None or d < best[0]:
                    best = (d, s.get("city") or "")
            if best:
                it["miles"], it["nearest"] = round(best[0]), best[1]
    return topics


def classify(items: list[dict], since: dt.date) -> None:
    for it in items:
        first = parse_date(it.get("first_posted"))
        last = parse_date(it.get("last_updated"))
        it["new"] = bool(first and first >= since)
        it["updated"] = bool(not it["new"] and last and last >= since)


def distance_bucket(it: dict) -> int:
    miles = it.get("miles")
    if miles is None:
        return 1
    return 0 if miles <= 45 else 1 if miles <= 90 else 2


def sort_key(it: dict):
    """Payment tier, then nearer first, then new before updated, then most recent."""
    date = parse_date(it.get("first_posted") if it.get("new") else it.get("last_updated"))
    return (TIER_ORDER.index(it["tier"]), distance_bucket(it), not it.get("new"),
            -(date or dt.date.min).toordinal())


def short_date(value: str | None) -> str:
    d = parse_date(value)
    return f"{d.day} {d.strftime('%b')}" if d else "?"


def where(it: dict, n: int = 3) -> str:
    if it.get("miles") is not None:
        return f"{it['nearest'] or 'Scotland'} (~{it['miles']} mi)"
    cities = sorted({s["city"] for s in it["sites"] if s["city"]})
    return ", ".join(cities[:n]) or "Scotland"


def describe(it: dict, number: int, labels: dict) -> str:
    """One numbered study, Slack-safe (bold number avoids list renumbering)."""
    bits = [where(it)]
    if it["phases"]:
        bits.append(phase_label(it["phases"]))
    if it["topics"]:
        bits.append(", ".join(labels.get(t, t) for t in it["topics"]))
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
    if it.get("flags"):
        line += f"\n⚠️ exclusions mention: {', '.join(it['flags'])}"
    if it["pay_evidence"]:
        line += f"\n_“{clean(it['pay_evidence'][0], 150)}”_"
    return line


def describe_short(it: dict, labels: dict) -> str:
    tags = [labels.get(t, t) for t in it["topics"]]
    if it["tier"] in {"paid", "likely_paid"}:
        tags.append("💷")
    if it.get("flags"):
        tags.append("⚠️")
    return (f"• {clean(it['title'], 80)} · {where(it, 2)}"
            f"{' · ' + ', '.join(tags) if tags else ''} · [{it['id']}]({it['url']})")


def build_sections(items: list[dict]) -> dict[str, list[dict]]:
    ok = [it for it in items if it.get("eligible", True)]
    week = [it for it in ok if it["new"] or it["updated"]]
    payish = sorted([it for it in week if it["tier"] in PAYISH_TIERS], key=sort_key)
    topical = sorted([it for it in week if it["topics"] and it["tier"] not in PAYISH_TIERS],
                     key=sort_key)
    shown = {it["id"] for it in payish + topical}
    other_new = sorted([it for it in ok if it["new"] and it["id"] not in shown],
                       key=sort_key)
    standing = sorted(
        [it for it in ok if not (it["new"] or it["updated"])
         and (it["topics"] or it["tier"] in {"paid", "likely_paid"})],
        # healthy-volunteer studies on my topics, then paid ones, then topic-only
        key=lambda it: (0 if it["topics"] and it["tier"] in PAYISH_TIERS
                        else 1 if it["tier"] in {"paid", "likely_paid"} else 2, sort_key(it)),
    )
    hidden = sum(1 for it in items if not it.get("eligible", True)
                 and (it["new"] or it["updated"]))
    return {"payish": payish, "topical": topical, "other_new": other_new,
            "standing": standing, "week": week, "hidden": hidden}


def render_markdown(items: list[dict], sources: dict, since: dt.date, today: dt.date,
                    max_other: int = 6, max_standing: int = 8, max_chars: int = 4800,
                    topics: list[dict] | None = None) -> str:
    """Slack digest. Shrinks the lower sections until it fits in one message."""
    sec = build_sections(items)
    labels = {t["key"]: t["label"] for t in (topics if topics is not None else TOPICS)}
    caps = {"payish": 10, "topical": 10, "other_new": max_other, "standing": max_standing}

    def render() -> str:
        hidden = (f" · {sec['hidden']} hidden (outside your age/sex)" if sec["hidden"] else "")
        out = [
            f"**🧪 Scotland trials · week to {today.strftime('%a')} {today.day} "
            f"{today.strftime('%b')}**",
            f"_{sum(i['new'] for i in items)} new and {sum(i['updated'] for i in items)} "
            f"updated studies recruiting in Scotland since {short_date(since.isoformat())} · "
            f"{len(sec['payish'])} for healthy volunteers or paid · "
            f"{sum(bool(i['topics']) for i in sec['week'])} on your topics{hidden}_",
            "",
        ]
        n = 0
        groups = [
            ("payish", "**💷 HEALTHY VOLUNTEERS & PAID**", "_Nothing new this week._"),
            ("topical", "**🎯 YOUR TOPICS**", "_Nothing new on your topics this week._"),
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
                out.append(describe(it, n, labels))
            out.append("")
        if sec["standing"] and caps["standing"]:
            rows = sec["standing"]
            more = (f" ({min(len(rows), caps['standing'])} of {len(rows)})"
                    if len(rows) > caps["standing"] else "")
            out.append("**📌 STILL OPEN: YOUR TOPICS & PAID**" + more)
            out.extend(describe_short(it, labels) for it in rows[:caps["standing"]])
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
    ap.add_argument("--profile", help="personal profile JSON (see module docstring)")
    ap.add_argument("--json", help="write all matching studies as JSON here")
    ap.add_argument("--markdown", help="write the Slack-ready digest here")
    ap.add_argument("--raw-json", help="write the merged studies with their matching text "
                    "(before any profile is applied), for offline testing")
    ap.add_argument("--max-other", type=int, default=6)
    ap.add_argument("--max-standing", type=int, default=8)
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    since = today - dt.timedelta(days=args.days)
    wanted = {s.strip() for s in args.sources.split(",") if s.strip()}
    profile = load_profile(args.profile)

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
    if args.raw_json:
        with open(args.raw_json, "w", encoding="utf-8") as f:
            json.dump({"today": today.isoformat(), "sources": sources, "studies": items}, f,
                      ensure_ascii=False)
    topics = apply_profile(items, profile)
    classify(items, since)
    items.sort(key=sort_key)
    sec = build_sections(items)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "since": since.isoformat(),
        "sources": sources,
        "topics": {t["key"]: t["label"] for t in topics},
        "counts": {
            "recruiting_in_scotland": len(items),
            "eligible": sum(i.get("eligible", True) for i in items),
            "new": sum(i["new"] for i in items),
            "updated": sum(i["updated"] for i in items),
            "payish_this_week": len(sec["payish"]),
            "topics_this_week": sum(bool(i["topics"]) for i in sec["week"]),
            "hidden_this_week": sec["hidden"],
        },
        "studies": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
    }
    md = render_markdown(items, sources, since, today, args.max_other, args.max_standing,
                         topics=topics)
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
