"""
Scraper for the NE hen harrier tracking update page. The page ships two
independent, parseable structures (live-verified 2026-07-10): an embedded
JSON-LD FAQPage schema, and plain HTML headings with following-sibling
paragraphs. We parse both and cross-check, since this is one of the doc's
flagged fragility points (irregular publishing, historical schema drift) -
disagreement between the two structures, or an unreachable .ods link,
should fail loudly rather than silently publishing a stale round.
"""

import json
import re

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.gov.uk/government/publications/hen-harriers-tracking-programme-update/hen-harrier-tracking-update"

USER_AGENT = "hen-harrier-tracker/0.1 (public-interest raptor-persecution tracking pipeline)"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_LABEL_RE = re.compile(r"(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})\s+update", re.IGNORECASE)
_ODS_HREF_RE = re.compile(r'href="([^"]+\.ods)"')


class ScraperError(RuntimeError):
    pass


def label_sort_key(label):
    """Parse an update label like "June 2026 update" into a (year, month) tuple for chronological sorting."""
    m = _LABEL_RE.search(label or "")
    if not m:
        return None
    month = _MONTHS.get(m.group("month").lower())
    if month is None:
        return None
    return (int(m.group("year")), month)


def _parse_jsonld_sections(html):
    """Return [(label, ods_url), ...] found in the JSON-LD FAQPage block."""
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") != "FAQPage":
            continue
        for question in data.get("mainEntity", []):
            label = question.get("name")
            answer_html = (question.get("acceptedAnswer") or {}).get("text", "")
            match = _ODS_HREF_RE.search(answer_html)
            if label and match:
                sections.append((label, match.group(1)))
    return sections


def _parse_html_sections(html):
    """Return [(label, ods_url), ...] found via the plain-HTML fallback structure."""
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for heading in soup.find_all(["h2", "h3"]):
        label = heading.get_text(strip=True)
        if not _LABEL_RE.search(label):
            continue
        ods_url = None
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3"):
                break
            link = sibling.find("a", href=re.compile(r"\.ods$"))
            if link:
                ods_url = link["href"]
                break
        if ods_url:
            sections.append((label, ods_url))
    return sections


def _pick_latest(sections):
    dated = [(label_sort_key(label), label, url) for label, url in sections]
    dated = [d for d in dated if d[0] is not None]
    if not dated:
        return None
    dated.sort(key=lambda d: d[0], reverse=True)
    return dated[0]


def find_latest_update(html=None, session=None):
    """
    Find the newest dated hen harrier tracking update on the gov.uk page.

    Returns {"label": str, "ods_url": str, "page_url": PAGE_URL}.

    Raises ScraperError if the JSON-LD and HTML structures disagree on which
    round is newest, or if neither yields any parseable section.
    """
    session = session or requests.Session()
    if html is None:
        resp = session.get(PAGE_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        html = resp.text

    jsonld_sections = _parse_jsonld_sections(html)
    html_sections = _parse_html_sections(html)

    if not jsonld_sections and not html_sections:
        raise ScraperError(
            "Neither JSON-LD nor HTML parsing found any dated update section on the gov.uk page - "
            "the page structure may have changed."
        )

    jsonld_latest = _pick_latest(jsonld_sections)
    html_latest = _pick_latest(html_sections)

    if jsonld_latest and html_latest and jsonld_latest[1] != html_latest[1]:
        raise ScraperError(
            f"JSON-LD and HTML structures disagree on the newest update: "
            f"JSON-LD says {jsonld_latest[1]!r}, HTML says {html_latest[1]!r}."
        )

    latest = jsonld_latest or html_latest
    if latest is None:
        raise ScraperError("Found dated sections but couldn't parse a (year, month) from any label.")

    _, label, ods_url = latest
    if ods_url.startswith("/"):
        ods_url = "https://www.gov.uk" + ods_url

    return {"label": label, "ods_url": ods_url, "page_url": PAGE_URL}


def find_all_updates(html=None, session=None):
    """
    Return every dated update section found (JSON-LD preferred, HTML as
    fallback if JSON-LD found nothing), newest first. Used for local
    backfilling/testing against more than one historical round.
    """
    session = session or requests.Session()
    if html is None:
        resp = session.get(PAGE_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        html = resp.text

    sections = _parse_jsonld_sections(html) or _parse_html_sections(html)
    dated = [(label_sort_key(label), label, url) for label, url in sections]
    dated = [d for d in dated if d[0] is not None]
    dated.sort(key=lambda d: d[0], reverse=True)

    results = []
    for _, label, ods_url in dated:
        if ods_url.startswith("/"):
            ods_url = "https://www.gov.uk" + ods_url
        results.append({"label": label, "ods_url": ods_url, "page_url": PAGE_URL})
    return results
