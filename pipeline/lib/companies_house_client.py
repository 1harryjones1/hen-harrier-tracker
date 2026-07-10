"""
Companies House API client for Stage E's ownership-candidate chain.

Used uniformly for CCOD company numbers and OCOD/Register of Overseas
Entities numbers - Companies House exposes overseas beneficial-owner PSC
records under the same list endpoint as ordinary UK company PSCs (the
`kind` field on each item distinguishes which regime applied), so there's
no separate OCOD code path here. (This is inferred from the API reference
docs, not tested against a real OE number yet - first thing to smoke-test
once a real OCOD-derived match exists.)

Auth: HTTP Basic, API key as username, password left blank - verified live
2026-07-10 against the official API reference docs. Rate limit: 600
requests/5 min across all endpoints combined, also verified live. Stage E
only ever runs against already-confirmed events (a handful a month),
nowhere near this limit, so only basic 429-handling is implemented here,
no proactive throttling.
"""

import base64
import os

import requests

API_BASE = "https://api.company-information.service.gov.uk"
USER_AGENT = "hen-harrier-tracker/0.1 (public-interest raptor-persecution tracking pipeline)"


class CompaniesHouseError(RuntimeError):
    pass


def _get_api_key():
    key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not key:
        raise CompaniesHouseError(
            "COMPANIES_HOUSE_API_KEY environment variable is not set - get a free key from "
            "developer.company-information.service.gov.uk and set it before running Stage E."
        )
    return key


def _auth_header():
    token = base64.b64encode(f"{_get_api_key()}:".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _get(path, session=None):
    session = session or requests.Session()
    resp = session.get(f"{API_BASE}{path}", headers={**_auth_header(), "User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code == 429:
        raise CompaniesHouseError(f"Companies House rate limit hit (429) for {path} - back off and retry later.")
    resp.raise_for_status()
    return resp.json()


def get_company_profile(company_number, session=None):
    """Basic company details - used to confirm a company/OE number is real before chasing officers/PSCs."""
    return _get(f"/company/{company_number}", session=session)


def get_officers(company_number, session=None):
    """Returns the officers list response dict, or None if the company number 404s (stale/mismatched data)."""
    return _get(f"/company/{company_number}/officers", session=session)


def get_persons_with_significant_control(company_number, session=None):
    """
    Returns the PSC list response dict, or None if 404.

    A PSC item whose `kind` is "individual-person-with-significant-control"
    or "individual-beneficial-owner" (Register of Overseas Entities regime)
    is a real named person. A `kind` containing "secure" (e.g.
    "super-secure-person-with-significant-control") means a PSC exists but
    is legally withheld from public disclosure for personal-safety reasons
    (gov.uk PSC exemptions guidance, confirmed live) - a real, if rare, case
    to surface honestly as "protected", never silently as "none found".
    Note: every PSC's *residential* address is always withheld regardless
    of kind (a service/correspondence address shows instead) - that's
    normal for every record, not a red flag, and distinct from the
    secure/super-secure exemption.
    """
    return _get(f"/company/{company_number}/persons-with-significant-control", session=session)


def has_protected_psc(psc_response):
    """True if any PSC item in the response is a secure/super-secure exemption record."""
    if not psc_response:
        return False
    return any("secure" in (item.get("kind") or "") for item in psc_response.get("items", []))
