"""
Parser for the NE hen harrier table's "OS Reference" column, which is
polymorphic across five observed shapes (verified against the live June 2026
spreadsheet):

  - British National Grid reference, e.g. "NY646926"          -> grid_reference
  - "N/A"                                                     -> not_applicable_alive
    (bird is alive/mobile, NE simply hasn't published a fixed point -
    this is NOT a redaction and NOT a parser bug)
  - Contains "confidential", e.g. "At nest (confidential)"    -> redacted_confidential
    (NE deliberately withholds precise roost/nest sites for species
    protection - also not a parser bug)
  - Free-text decimal lat/lon in inconsistent formats, for birds whose last
    fix was outside Great Britain (BNG has no meaning there):
      "N57.74904 W19.81996"
      "Lat:41.46132 Long. -3.51336"
      "54.60405°, -0.35892°"
      "Lat: 49.48632 Long: 0.41911"
                                                               -> decimal_latlon
  - Anything else                                             -> unparseable
    (a real parser gap - should be rare/zero in steady state, unlike the
    two deliberate-non-disclosure cases above)
"""

import re

from .os_grid import grid_ref_to_latlon

LOCATION_STATUS_GRID_REFERENCE = "grid_reference"
LOCATION_STATUS_DECIMAL_LATLON = "decimal_latlon"
LOCATION_STATUS_REDACTED_CONFIDENTIAL = "redacted_confidential"
LOCATION_STATUS_NOT_APPLICABLE_ALIVE = "not_applicable_alive"
LOCATION_STATUS_UNPARSEABLE = "unparseable"

_COMPASS_PAIR_RE = re.compile(
    r"([NS])\s*(\d+\.?\d*)\D{0,3}([EW])\s*(\d+\.?\d*)", re.IGNORECASE
)
_LAT_LONG_LABELLED_RE = re.compile(
    r"Lat\.?:?\s*(-?\d+\.?\d*)[,\s]+Long\.?:?\s*(-?\d+\.?\d*)", re.IGNORECASE
)
_PLAIN_DECIMAL_PAIR_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*°?\s*,\s*(-?\d{1,3}\.\d+)\s*°?")


def parse_decimal_latlon(text):
    """Parse free-text decimal lat/lon in any of the observed NE formats. Returns (lat, lon) or None."""
    m = _COMPASS_PAIR_RE.search(text)
    if m:
        ns, lat_val, ew, lon_val = m.groups()
        lat = float(lat_val) * (1 if ns.upper() == "N" else -1)
        lon = float(lon_val) * (1 if ew.upper() == "E" else -1)
        return lat, lon

    m = _LAT_LONG_LABELLED_RE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = _PLAIN_DECIMAL_PAIR_RE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None


def classify_location(raw_text):
    """
    Classify and parse an OS Reference field value.

    Returns a dict:
      {
        "location_status": one of the LOCATION_STATUS_* constants,
        "coordinates": {"lat": float, "lon": float} or None,
        "precision_m": int or None,
      }
    """
    text = (raw_text or "").strip()

    if not text or text.upper() == "N/A":
        return {
            "location_status": LOCATION_STATUS_NOT_APPLICABLE_ALIVE,
            "coordinates": None,
            "precision_m": None,
        }

    if "confidential" in text.lower():
        return {
            "location_status": LOCATION_STATUS_REDACTED_CONFIDENTIAL,
            "coordinates": None,
            "precision_m": None,
        }

    grid = grid_ref_to_latlon(text)
    if grid is not None:
        lat, lon, precision_m = grid
        return {
            "location_status": LOCATION_STATUS_GRID_REFERENCE,
            "coordinates": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "precision_m": precision_m,
        }

    decimal = parse_decimal_latlon(text)
    if decimal is not None:
        lat, lon = decimal
        return {
            "location_status": LOCATION_STATUS_DECIMAL_LATLON,
            "coordinates": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "precision_m": None,
        }

    return {
        "location_status": LOCATION_STATUS_UNPARSEABLE,
        "coordinates": None,
        "precision_m": None,
    }
