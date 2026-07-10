"""
ArcGIS Hub/Online client for the NE Open Data Geoportal layers used in
Stage D (geolocation cross-reference).

Discovers the current FeatureServer URL for a named layer via the plain-JSON
ArcGIS Online Sharing REST search API, rather than scraping the dataset
"about" pages (those are JS-rendered Hub pages that don't yield a
FeatureServer URL to a simple fetch - verified live 2026-07-10). This also
makes "the dataset name/URL changes every year" (true of the Moorland Change
Map, per the doc's §3/§9) self-healing instead of a silent 404: re-running
the search each month picks up whatever the current item is.

Habitat matching itself needs no local geometry library - the FeatureServer
query operation does point-in-polygon server-side, and reprojects a WGS84
point query against a native British National Grid service correctly
(verified live).
"""

import re

import requests

SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"
USER_AGENT = "hen-harrier-tracker/0.1 (public-interest raptor-persecution tracking pipeline)"

_YEAR_RANGE_RE = re.compile(r"\((\d{4})-(\d{4})\)\s*$")


class ArcGISError(RuntimeError):
    pass


def search_items(title, owner="Opendata_NE", num=20, session=None):
    """Search the ArcGIS Online Sharing REST API for items matching a title, newest-modified first."""
    session = session or requests.Session()
    query = f'title:"{title}" AND owner:{owner}'
    resp = session.get(
        SEARCH_URL,
        params={"f": "json", "q": query, "num": num, "sortField": "modified", "sortOrder": "desc"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ArcGISError(f"ArcGIS search API error for title={title!r}: {data['error']}")
    return data.get("results", [])


def _year_range_key(item_title):
    m = _YEAR_RANGE_RE.search(item_title)
    return int(m.group(2)) if m else None  # end year, if the title has a "(YYYY-YYYY)" suffix


def discover_layer(title, owner="Opendata_NE", exact_title_prefix=None, session=None):
    """
    Discover the current FeatureServer URL for a named NE layer.

    `title` is the search query. Some searches match multiple related
    datasets (e.g. "CRoW Act" alone matches 6 - Access Layer, Section 4/15/16
    layers, etc.) - pass `exact_title_prefix` to filter to the one whose
    title starts with that exact string. Among remaining candidates, prefers
    the item with the highest "(YYYY-YYYY)" year-range suffix if present
    (this is how the annually-republished Moorland Change Map is named),
    else the most recently modified item.

    Returns {"item_id", "title", "url", "modified"}. Raises ArcGISError if
    no matching Feature Service item is found.
    """
    results = search_items(title, owner=owner, session=session)
    results = [r for r in results if r.get("type") == "Feature Service"]
    if exact_title_prefix:
        results = [r for r in results if r.get("title", "").startswith(exact_title_prefix)]
    if not results:
        raise ArcGISError(f"No Feature Service item found for title={title!r} (owner={owner!r}).")

    dated = [(_year_range_key(r["title"]), r) for r in results]
    if any(key is not None for key, _ in dated):
        dated = [d for d in dated if d[0] is not None]
        dated.sort(key=lambda d: d[0], reverse=True)
        chosen = dated[0][1]
    else:
        results.sort(key=lambda r: r.get("modified", 0), reverse=True)
        chosen = results[0]

    url = chosen.get("url")
    if not url:
        raise ArcGISError(f"Item {chosen.get('id')} ({chosen.get('title')!r}) has no FeatureServer URL.")

    return {"item_id": chosen["id"], "title": chosen["title"], "url": url, "modified": chosen.get("modified")}


def query_point(feature_server_url, lat, lon, layer_index=0, session=None):
    """
    Query a FeatureServer layer for features intersecting a point.

    Returns the raw GeoJSON response dict (an empty `features` list means no
    match, not an error).
    """
    session = session or requests.Session()
    query_url = f"{feature_server_url.rstrip('/')}/{layer_index}/query"
    resp = session.get(
        query_url,
        params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "geojson",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ArcGISError(f"ArcGIS query error for {query_url}: {data['error']}")
    return data
