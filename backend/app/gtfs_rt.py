"""GTFS-RT TripUpdates: fetch, decode, keep Saarland rail trips only.

The gtfs.de free stream has no route_ids, so we join trip_id -> static
trips.txt (rail feeds de_rv/de_fv) and require at least one stop inside
the Saarland bbox (exact stop_id match).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from google.transit import gtfs_realtime_pb2

from . import config, gtfs_static

log = logging.getLogger(__name__)

_last_fetch = 0.0
_cache: list[dict[str, Any]] = []


async def fetch() -> list[dict[str, Any]]:
    """Fetch GTFS-RT and return Saarland rail trip updates (cached ~9s)."""
    global _last_fetch, _cache
    now = time.time()
    if now - _last_fetch < 9 and _cache:
        return _cache

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(config.GTFS_RT_URL)
            r.raise_for_status()
            raw = r.content
    except Exception as e:  # noqa: BLE001
        log.warning("GTFS-RT fetch failed: %s", e)
        return _cache

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("GTFS-RT parse failed: %s", e)
        return _cache

    stop_ids = gtfs_static.stop_ids()
    updates: list[dict[str, Any]] = []

    for entity in feed.entity:
        tu = entity.trip_update
        if not tu.HasField("trip"):
            continue
        trip = tu.trip

        # rail trips only (joins de_rv/de_fv static feeds)
        if gtfs_static.trip_route(trip.trip_id) is None:
            continue

        # must touch Saarland (exact stop match — no fuzzy suffix matching)
        if not any(st.stop_id in stop_ids for st in tu.stop_time_update):
            continue

        stops_out = []
        for st in tu.stop_time_update:
            entry: dict[str, Any] = {"stop_id": st.stop_id}
            if st.HasField("arrival"):
                entry["arrival"] = st.arrival.time
                entry["arrival_delay"] = st.arrival.delay
            if st.HasField("departure"):
                entry["departure"] = st.departure.time
                entry["departure_delay"] = st.departure.delay
            stops_out.append(entry)

        updates.append(
            {
                "trip_id": trip.trip_id,
                "route_id": gtfs_static.trip_route(trip.trip_id),
                "start_time": trip.start_time,
                "start_date": trip.start_date,
                "direction_id": trip.direction_id if trip.HasField("direction_id") else None,
                "stops": stops_out,
            }
        )

    _cache = updates
    _last_fetch = now
    return _cache


def delay_stats() -> dict[str, Any]:
    """Aggregate delay stats from the cached Saarland rail updates."""
    delays = []
    for u in _cache:
        for s in u["stops"]:
            d = s.get("arrival_delay")
            if d is None:
                d = s.get("departure_delay")
            if d is not None:
                delays.append(d)
                break  # one entry per trip
    delayed = sum(1 for d in delays if d >= 120)
    return {
        "trips": len(_cache),
        "with_delay_data": len(delays),
        "delayed_ge_2min": delayed,
        "max_delay_s": max(delays) if delays else None,
    }
