"""Neighborhood lookup from a listing's lat/lng using hoodmaps.com's public
Zürich GeoJSON (the same file their map page renders from).

The categories are theirs: rich | hipsters | normies | tourists | suits | crime
(and potentially others if they expand). We store both the neighborhood NAME
and the CATEGORY on each listing; the digest shows them at a glance.

Polite: the GeoJSON is downloaded ONCE and cached in data/cache/, refreshed
only every 30 days. Lookups thereafter are local-only.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import paths

SOURCE_URL = ("https://hoodmaps.com/assets/districts_categorized/"
              "zurich.geojson")
CACHE = paths.DATA_DIR / "cache" / "zurich.hoodmaps.geojson"
MAX_AGE_DAYS = 30

_features: list | None = None


def _download() -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(SOURCE_URL, headers={
        "User-Agent": "Mozilla/5.0 (zrh-apts-hoods/1.0)",
        "Referer": "https://hoodmaps.com/zurich-neighborhood-map",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        CACHE.write_bytes(resp.read())


def _ensure_loaded() -> list:
    global _features
    if _features is not None:
        return _features
    fresh = CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            _download()
        except Exception:
            if not CACHE.exists():
                _features = []
                return _features  # quietly degrade; lookups will return (None, None)
    try:
        _features = json.loads(CACHE.read_text(encoding="utf-8")).get("features", [])
    except Exception:
        _features = []
    return _features


# --- geometry ---------------------------------------------------------------
def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-casting point-in-ring; ring is [[lon,lat], ...]."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            x_at_y = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_at_y:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """polygon = [outer_ring, hole_ring_1, ...]. Outer must contain, holes must not."""
    if not polygon:
        return False
    if not _point_in_ring(x, y, polygon[0]):
        return False
    for hole in polygon[1:]:
        if _point_in_ring(x, y, hole):
            return False
    return True


# --- public ----------------------------------------------------------------
def lookup(lat, lon) -> tuple[str | None, str | None]:
    """Return (neighborhood_name, category) or (None, None) if no match / no coords.
    GeoJSON coords are [lon, lat]; we treat x=lon, y=lat."""
    try:
        x, y = float(lon), float(lat)
    except (TypeError, ValueError):
        return (None, None)
    for f in _ensure_loaded():
        p = f.get("properties") or {}
        # Cheap bbox prefilter (the GeoJSON ships sw_/ne_ bounds per feature).
        if "sw_lat" in p and "ne_lat" in p and "sw_lng" in p and "ne_lng" in p:
            if not (p["sw_lat"] <= y <= p["ne_lat"] and p["sw_lng"] <= x <= p["ne_lng"]):
                continue
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "MultiPolygon":
            for polygon in coords:
                if _point_in_polygon(x, y, polygon):
                    return (p.get("name"), p.get("category"))
        elif gtype == "Polygon":
            if _point_in_polygon(x, y, coords):
                return (p.get("name"), p.get("category"))
    return (None, None)
