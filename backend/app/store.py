"""In-memory state + asyncio pollers for HAFAS radar and GTFS-RT."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import config, gtfs_rt, gtfs_static, hafas

log = logging.getLogger(__name__)

_state: dict[str, Any] = {
    "vehicles": [],
    "trip_updates": [],
    "delay_stats": {},
    "radar_updated_at": 0.0,
    "gtfs_rt_updated_at": 0.0,
    "radar_error": None,
    "gtfs_rt_error": None,
}
_listeners: set[asyncio.Queue] = set()
_hafas = hafas.HafasClient()


def snapshot() -> dict[str, Any]:
    return dict(_state)


async def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _listeners.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _listeners.discard(q)


async def _broadcast() -> None:
    payload = snapshot()
    for q in list(_listeners):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # slow client: drop, it will resync via /api/vehicles


def _delay_stats(vehicles: list[dict[str, Any]]) -> dict[str, Any]:
    delays = [v["delay_s"] for v in vehicles if v.get("delay_s") is not None]
    return {
        "vehicles": len(vehicles),
        "with_delay_data": len(delays),
        "delayed_ge_2min": sum(1 for d in delays if d >= 120),
        "max_delay_s": max(delays) if delays else None,
    }


async def radar_loop() -> None:
    while True:
        started = time.monotonic()
        try:
            raw, common = await _hafas.radar(config.SAARLAND_BBOX)
            vehicles = []
            for entry in raw:
                v = hafas.parse_vehicle(entry, common)
                if v:
                    v["color"] = gtfs_static.route_color_for_line(v["line"])
                    vehicles.append(v)
            _state["vehicles"] = vehicles
            _state["delay_stats"] = _delay_stats(vehicles)
            _state["radar_updated_at"] = time.time()
            _state["radar_error"] = None
            log.info("radar: %d vehicles", len(vehicles))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _state["radar_error"] = str(e)
            log.warning("radar failed: %s", e)
        await _broadcast()
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, config.RADAR_INTERVAL - elapsed))


async def gtfs_rt_loop() -> None:
    while True:
        started = time.monotonic()
        try:
            updates = await gtfs_rt.fetch()
            _state["trip_updates"] = updates
            _state["delay_stats"] = gtfs_rt.delay_stats()
            _state["gtfs_rt_updated_at"] = time.time()
            _state["gtfs_rt_error"] = None
            log.info("gtfs-rt: %d saarland rail trip updates", len(updates))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _state["gtfs_rt_error"] = str(e)
            log.warning("gtfs-rt failed: %s", e)
        await _broadcast()
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, config.GTFS_RT_INTERVAL - elapsed))


async def start() -> None:
    """Load static GTFS (blocking-ish, run in thread) then start pollers."""
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, gtfs_static.load)
    if not ok:
        log.warning("static GTFS not loaded — stops layer will be empty")
    asyncio.create_task(radar_loop(), name="radar")
    if config.ENABLE_GTFS_RT:
        asyncio.create_task(gtfs_rt_loop(), name="gtfs-rt")
    else:
        log.info("GTFS-RT polling disabled (ENABLE_GTFS_RT=False)")


async def stop() -> None:
    await _hafas.aclose()
