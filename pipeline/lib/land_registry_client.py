"""
HM Land Registry client for Stage E's ownership-candidate chain.

resolve_local_authority() reuses the existing arcgis_client.py machinery
against the ONS Local Authority Districts boundary layer (cached in
pipeline/config/arcgis_layers.json alongside the habitat layers, discovered
by fetch_moorland_change.py) - the same "discover once, query per-point"
pattern as Stage D, no new HTTP client code needed for this step. Verified
live 2026-07-10 against bird 213924's real confirmed coordinates, correctly
resolving to "North Yorkshire" (matching its already-confirmed North York
Moors habitat match).

find_title_number() is the one genuinely uncertain piece in this module:
INSPIRE Index Polygons have no point-query API (confirmed - they're split
into ~329 separate per-local-authority GML files with no FeatureServer
equivalent), so resolving a title number means downloading the one relevant
LA's file and doing point-in-polygon locally via shapely/fiona. The exact
download URL pattern and the GML field name for the title number could not
be verified this session (the download portal blocked automated access) -
see the docstring below for what's assumed vs confirmed.

lookup_ccod_ocod() is fully built and tested against a hand-built fixture
matching HMLR's real documented CCOD/OCOD column schema (title number,
tenure, up to 4 numbered proprietor slots per title since titles can have
joint proprietors) - swap FIXTURE_MODE off once a real account exists.
"""

import csv
import json
import os
import zipfile
from io import BytesIO, StringIO

import requests

from . import raw_snapshot
from .arcgis_client import ArcGISError, query_point

REPO_ROOT = raw_snapshot.repo_root()
ARCGIS_CONFIG_PATH = os.path.join(REPO_ROOT, "pipeline", "config", "arcgis_layers.json")
LA_GML_CONFIG_PATH = os.path.join(REPO_ROOT, "pipeline", "config", "local_authority_gml_files.json")
BULK_CACHE_DIR = os.path.join(REPO_ROOT, ".cache", "land_registry_bulk")

USER_AGENT = "hen-harrier-tracker/0.1 (public-interest raptor-persecution tracking pipeline)"

# HMLR's real CCOD/OCOD schema has up to 4 numbered proprietor slots per
# title (joint proprietors), each with its own Name/Company Registration
# No./Proprietorship Category/Country Incorporated/Address fields.
MAX_PROPRIETOR_SLOTS = 4


class LandRegistryError(RuntimeError):
    pass


def _load_arcgis_layers():
    if not os.path.exists(ARCGIS_CONFIG_PATH):
        return {}
    with open(ARCGIS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_local_authority(lat, lon):
    """
    Resolve a coordinate to its Local Authority District (name + ONS code),
    via the point-query mechanism already proven in arcgis_client.py,
    against the layer cached under "local_authority_districts" in
    arcgis_layers.json (populated by fetch_moorland_change.py).

    Returns {"name": str, "code": str} or None if no LA contains the point
    (shouldn't happen for a genuine GB coordinate - flag as an anomaly if
    it ever does, mirroring diff_and_detect.py's anomaly-logging principle
    rather than crashing).
    """
    layers = _load_arcgis_layers()
    layer_info = layers.get("local_authority_districts")
    if layer_info is None:
        raise LandRegistryError(
            "local_authority_districts layer not yet discovered - run fetch_moorland_change.py"
        )

    try:
        response = query_point(layer_info["url"], lat=lat, lon=lon)
    except (ArcGISError, requests.RequestException) as exc:
        raise LandRegistryError(f"Local authority lookup failed: {exc}") from exc

    features = response.get("features", [])
    if not features:
        return None
    props = features[0]["properties"]
    return {"name": props.get("LAD24NM"), "code": props.get("LAD24CD")}


def _load_la_gml_config():
    if not os.path.exists(LA_GML_CONFIG_PATH):
        return {}
    with open(LA_GML_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_title_number(lat, lon, la_code, session=None):
    """
    Find the INSPIRE title number whose polygon contains (lat, lon), within
    local authority `la_code`.

    UNVERIFIED (the download portal blocked automated access this session -
    confirm against a real downloaded file before trusting this in
    production):
      - the download URL for a given LA's GML file - la_gml_config must map
        la_code to a real, working download URL (see
        pipeline/config/local_authority_gml_files.json - starts empty,
        populated by hand, one LA at a time, only for LAs where a real
        confirmed event occurs)
      - the GML property name for the title number - tries several likely
        candidates (INSPIRE's own spec uses "INSPIREID"; HMLR's public
        guidance refers to "title number") rather than assuming one, and
        raises loudly listing the actual available property names if none
        of the candidates match, so this is a one-line fix once a real
        file shows what the field is actually called.

    Returns the title number string, or None if no polygon in this LA's
    file contains the point (genuinely unregistered land).
    """
    import fiona
    from bng_latlon import WGS84toOSGB36
    from shapely.geometry import Point, shape

    la_gml_config = _load_la_gml_config()
    file_info = la_gml_config.get(la_code)
    if file_info is None:
        raise LandRegistryError(
            f"No INSPIRE GML file configured for local authority {la_code!r} - "
            f"add it to {LA_GML_CONFIG_PATH} (download the file from "
            "use-land-property-data.service.gov.uk/datasets/inspire/download and record its URL)."
        )

    gml_path = _ensure_la_gml_downloaded(la_code, file_info, session=session)

    # The GML polygons are in British National Grid (EPSG:27700) easting/northing
    # metres, NOT WGS84 lat/lon degrees - confirmed live 2026-07-11 (a plain
    # centroid check on real downloaded data showed six-figure metre values,
    # not degree values). Reproject the query point once rather than every
    # polygon - reusing the same bng_latlon package os_grid.py already
    # depends on for the reverse direction.
    easting, northing = WGS84toOSGB36(lat, lon)
    point = Point(easting, northing)

    title_field_candidates = ("INSPIREID", "TITLE_NO", "TITLE_NUMBER", "title_no", "title_number")
    with fiona.open(gml_path) as source:
        for feature in source:
            geom = shape(feature["geometry"])
            if not geom.contains(point):
                continue
            props = dict(feature["properties"])
            for candidate in title_field_candidates:
                if candidate in props:
                    return props[candidate]
            raise LandRegistryError(
                f"Found a containing polygon in {la_code!r} but none of the expected title-number "
                f"fields {title_field_candidates} exist - actual fields: {sorted(props.keys())}. "
                "Update title_field_candidates in find_title_number() to match the real schema."
            )
    return None


def _ensure_la_gml_downloaded(la_code, file_info, session=None):
    """Download (once, cached) and extract a local authority's INSPIRE GML zip. Never committed to git -
    lives under .cache/, which is gitignored (see doc §7's repo-size discipline)."""
    os.makedirs(BULK_CACHE_DIR, exist_ok=True)
    zip_path = os.path.join(BULK_CACHE_DIR, f"{la_code}.zip")
    extract_dir = os.path.join(BULK_CACHE_DIR, la_code)

    if not os.path.exists(zip_path):
        session = session or requests.Session()
        resp = session.get(file_info["download_url"], headers={"User-Agent": USER_AGENT}, timeout=120)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(resp.content)

    if not os.path.isdir(extract_dir):
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(extract_dir)

    for name in os.listdir(extract_dir):
        if name.lower().endswith(".gml"):
            return os.path.join(extract_dir, name)
    raise LandRegistryError(f"No .gml file found in downloaded archive for {la_code!r} (looked in {extract_dir}).")


def _collect_proprietors(row):
    """Collect all non-empty numbered proprietor slots (HMLR's real CCOD/OCOD schema allows joint
    proprietors, up to 4 slots per title, each independently populated)."""
    proprietors = []
    for slot in range(1, MAX_PROPRIETOR_SLOTS + 1):
        name = (row.get(f"Proprietor Name ({slot})") or "").strip()
        if not name:
            continue
        proprietors.append({
            "slot": slot,
            "name": name,
            "company_registration_no": (row.get(f"Company Registration No. ({slot})") or "").strip() or None,
            "proprietorship_category": (row.get(f"Proprietorship Category ({slot})") or "").strip() or None,
            "country_incorporated": (row.get(f"Country Incorporated ({slot})") or "").strip() or None,
        })
    return proprietors


def lookup_ccod_ocod(title_number, ccod_path=None, ocod_path=None):
    """
    Streaming-scan CCOD then OCOD for `title_number` - no database needed at
    this volume (a handful of confirmed events a month; see the plan for why
    an index/sqlite upgrade is a future concern, not a v1 one).

    Returns {"dataset": "ccod"|"ocod", "row": {...}, "proprietors": [...]}
    or None if the title isn't in either file - the honest, structurally-
    expected outcome for individual/trust/charity ownership (CCOD/OCOD
    together exclude private individuals; CCOD also excludes charities),
    not a miss to retry.
    """
    for dataset, path in (("ccod", ccod_path), ("ocod", ocod_path)):
        if not path or not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Title Number", "").strip() == title_number:
                    return {"dataset": dataset, "row": row, "proprietors": _collect_proprietors(row)}
    return None
