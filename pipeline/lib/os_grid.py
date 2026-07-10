"""British National Grid reference <-> WGS84 lat/lon conversion.

Reference: Ordnance Survey "A guide to coordinate systems in Great Britain",
Appendix C (grid letter lettering scheme). The ellipsoidal OSGB36->WGS84
transform is delegated to the `bng-latlon` package rather than hand-rolled,
since a subtle geodetic error would silently mislocate every marker.
"""

import re

from bng_latlon import OSGB36toWGS84

# Two letters + an even number of digits (optionally space-separated),
# e.g. "NY646926", "NY 646 926", "TQ1234567890".
_GRID_REF_RE = re.compile(r"^([A-Za-z]{2})(\d+)$")


def grid_letters_to_100km(letters):
    """Return (e100k, n100k): the 100km-square origin, in units of 100km."""
    l1 = ord(letters[0].upper()) - ord("A")
    l2 = ord(letters[1].upper()) - ord("A")
    # The letter "I" is skipped in the OS lettering scheme.
    if l1 > 7:
        l1 -= 1
    if l2 > 7:
        l2 -= 1
    e100k = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100k = (19 - (l1 // 5) * 5) - (l2 // 5)
    return e100k, n100k


def parse_grid_ref(ref):
    """
    Parse a British National Grid reference (e.g. "NY646926") into
    (easting, northing, precision_m). Returns None if `ref` doesn't match
    the two-letter + even-digit-count grid reference shape.
    """
    if not ref:
        return None
    cleaned = re.sub(r"\s+", "", ref.strip())
    match = _GRID_REF_RE.match(cleaned)
    if not match:
        return None
    letters, digits = match.groups()
    if len(digits) == 0 or len(digits) % 2 != 0 or len(digits) > 10:
        return None
    half = len(digits) // 2
    easting_digits = digits[:half]
    northing_digits = digits[half:]
    e100k, n100k = grid_letters_to_100km(letters)
    scale = 10 ** (5 - half)
    easting = e100k * 100_000 + int(easting_digits) * scale
    northing = n100k * 100_000 + int(northing_digits) * scale
    return easting, northing, scale


def grid_ref_to_latlon(ref):
    """
    Convert a British National Grid reference string to (lat, lon, precision_m).
    Returns None if the reference can't be parsed.
    """
    parsed = parse_grid_ref(ref)
    if parsed is None:
        return None
    easting, northing, precision_m = parsed
    lat, lon = OSGB36toWGS84(easting, northing)
    return lat, lon, precision_m
