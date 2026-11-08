"""Static GTFS: download gtfs.de feeds, cache, bbox-filter stops/routes."""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)

_stops: dict[str, dict[str, Any]] = {}
_routes: dict[str, dict[str, Any]] = {}
_trip_route: dict[str, str] = {}  # trip_id -> route_id (both feeds)
_ready = False


def _in_bbox(lat: float, lon: float) -> bool:
    min_lat, min_lon, max_lat, max_lon = config.SAARLAND_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _download(name: str, url: str) -> bytes | None:
    path = config.DATA_DIR / f"{name}.zip"
    if path.exists() and path.stat().st_size > 100_000:
        return path.read_bytes()
    try:
        r = httpx.get(url, timeout=180, follow_redirects=True)
        r.raise_for_status()
        if len(r.content) < 100_000:
            log.warning("GTFS download for %s suspiciously small (%d bytes)", name, len(r.content))
            return None
        path.write_bytes(r.content)
        log.info("downloaded %s (%.1f MB)", name, len(r.content) / 1e6)
        return r.content
    except Exception as e:  # noqa: BLE001
        log.warning("GTFS download failed for %s: %s", name, e)
        return None


def _read_csv(zf: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    try:
        raw = zf.read(member)
    except KeyError:
        return []
    text = io.StringIO(raw.decode("utf-8-sig", errors="replace"))
    return list(csv.DictReader(text))


def load() -> bool:
    """Load + filter feeds into memory. Returns True if stops loaded."""
    global _ready
    if _ready:
        return True

    for name, url in config.GTFS_FEEDS.items():
        data = _download(name, url)
        if not data:
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            log.warning("bad zip for %s", name)
            continue

        for s in _read_csv(zf, "stops.txt"):
            try:
                lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
            except (KeyError, ValueError):
                continue
            if not _in_bbox(lat, lon):
                continue
            sid = s.get("stop_id") or ""
            _stops[sid] = {
                "id": sid,
                "name": s.get("stop_name") or "",
                "lat": lat,
                "lon": lon,
            }

        for r in _read_csv(zf, "routes.txt"):
            rid = r.get("route_id") or ""
            _routes[rid] = {
                "id": rid,
                "short_name": (r.get("route_short_name") or "").strip(),
                "long_name": r.get("route_long_name") or "",
                "color": "#" + (r.get("route_color") or "1f6feb"),
                "text_color": "#" + (r.get("route_text_color") or "ffffff"),
                "type": r.get("route_type") or "",
            }

        for t in _read_csv(zf, "trips.txt"):
            tid, rid = t.get("trip_id") or "", t.get("route_id") or ""
            if tid and rid:
                _trip_route[tid] = rid

    _ready = True
    log.info(
        "GTFS loaded: %d stops, %d routes, %d trips",
        len(_stops),
        len(_routes),
        len(_trip_route),
    )
    return bool(_stops)


def stops() -> list[dict[str, Any]]:
    return list(_stops.values())


def routes() -> dict[str, dict[str, Any]]:
    return _routes


def stop_ids() -> set[str]:
    return set(_stops)


def trip_route(trip_id: str) -> str | None:
    """route_id for a gtfs.de trip_id, or None if not in the static feeds."""
    return _trip_route.get(trip_id)


def route_color_for_line(line: str) -> str | None:
    """Match an HAFAS line label (e.g. 'RB68') to a GTFS route color."""
    norm = line.replace(" ", "").upper()
    for r in _routes.values():
        if r["short_name"].replace(" ", "").upper() == norm:
            return r["color"]
    return None


def ready() -> bool:
    return _ready
