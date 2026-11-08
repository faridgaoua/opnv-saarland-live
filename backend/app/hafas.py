"""Minimal HAFAS mgate.exe client (saarfahrplan) with mic/mac auth.

Request shapes ported from public-transport/hafas-client (p/saarfahrplan,
format/radar-req.js, format/rectangle.js, lib/request.js).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from . import config


class HafasError(RuntimeError):
    pass


def _signed_url(body_bytes: bytes) -> str:
    """mic = md5(body); mac = md5(mic_hex + salt) — appended as query params."""
    mic = hashlib.md5(body_bytes).digest()
    mac = hashlib.md5(mic.hex().encode("utf-8") + config.HAFAS_SALT).digest()
    return f"{config.HAFAS_ENDPOINT}?mic={mic.hex()}&mac={mac.hex()}"


def _base_body(svc_req: dict[str, Any]) -> dict[str, Any]:
    return {
        "lang": config.HAFAS_LANG,
        "svcReqL": [svc_req],
        "client": config.HAFAS_CLIENT,
        "ver": config.HAFAS_VER,
        "auth": {"type": "AID", "aid": config.HAFAS_AID},
    }


def _check(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if body.get("err") and body["err"] != "OK":
        raise HafasError(f"HAFAS error {body['err']}: {body.get('errTxt', '')}")
    svc = (body.get("svcResL") or [None])[0]
    if not svc:
        raise HafasError("invalid response structure")
    if svc.get("err") != "OK":
        raise HafasError(f"HAFAS svc error {svc.get('err')}: {svc.get('errTxt', '')}")
    res = svc.get("res") or {}
    return res, res.get("common") or {}


def _hhmmss_to_sec(t: str | None) -> int | None:
    """'140230' -> seconds since midnight; handles day offsets like '240230'."""
    if not t or len(t) < 6:
        return None
    try:
        h, m, s = int(t[0:2]), int(t[2:4]), int(t[4:6])
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google-encoded polyline -> [(lat, lon), ...] (as in hafas-client)."""
    coords: list[tuple[float, float]] = []
    index = 0
    lat = lng = 0
    try:
        while index < len(encoded):
            for axis in range(2):
                shift = 0
                result = 0
                while True:
                    b = ord(encoded[index]) - 63
                    index += 1
                    result |= (b & 0x1F) << shift
                    shift += 5
                    if b < 0x20:
                        break
                delta = ~(result >> 1) if result & 1 else (result >> 1)
                if axis == 0:
                    lat += delta
                else:
                    lng += delta
            coords.append((lat / 10**precision, lng / 10**precision))
    except (IndexError, ValueError):
        pass
    return coords


class HafasClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": config.HAFAS_USER_AGENT},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, meth: str, req: dict[str, Any]) -> tuple[dict, dict]:
        body = _base_body({"meth": meth, "req": req})
        body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        url = _signed_url(body_bytes)
        resp = await self._client.post(
            url,
            content=body_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        resp.raise_for_status()
        return _check(resp.json())

    async def radar(
        self,
        bbox: tuple[float, float, float, float],
        products: int = config.TRANSIT_MASK,
        results: int = 250,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """JourneyGeoPos: live vehicle positions inside the bbox.

        Returns (journeys, common) — common holds prodL/locL/polyL lookups.
        """
        min_lat, min_lon, max_lat, max_lon = bbox
        res, common = await self._call(
            "JourneyGeoPos",
            {
                "maxJny": results,
                "onlyRT": False,
                "date": time.strftime("%Y%m%d"),
                "time": time.strftime("%H%M%S"),
                "rect": {
                    "llCrd": {"x": round(min_lon * 1e6), "y": round(min_lat * 1e6)},
                    "urCrd": {"x": round(max_lon * 1e6), "y": round(max_lat * 1e6)},
                },
                "perSize": 30000,
                "perStep": 10000,
                "ageOfReport": True,
                "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": str(products)}],
                "trainPosMode": "CALC",
            },
        )
        return res.get("jnyL") or [], common

    async def departures(self, stop_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """StationBoard: next departures with realtime delay at a stop."""
        res, _common = await self._call(
            "StationBoard",
            {
                "type": "DEP",
                "stbLoc": {"lid": f"A=1@O={stop_id}"},
                "maxNum": limit,
                "time": time.strftime("%H%M%S"),
                "dateB": time.strftime("%Y%m%d"),
                "prodL": [{"mode": "INC", "value": str(config.TRANSIT_MASK)}],
            },
        )
        return res.get("journey") or []

    async def locations(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """LocMatch: stop/station name search."""
        res, _common = await self._call(
            "LocMatch",
            {"inputField": "input", "locS": [], "criteria": {"input": query}, "maxLoc": limit},
        )
        return res.get("matchLocL") or []


def _mode_from_cls(cls: int) -> str:
    """HAFAS product class -> coarse UI mode."""
    if cls == 16:
        return "sbahn"
    if cls in (8, 32, 64, 256):  # regional rail + tram/subway-ish -> rail family
        return "bahn"
    if cls in (4, 128):  # bus (incl. Fernbus)
        return "bus"
    if cls == 512:  # Anruf-Sammel-Taxi
        return "ast"
    return "other"


def parse_vehicle(jny: dict[str, Any], common: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a JourneyGeoPos entry into a frontend vehicle object."""
    pos = jny.get("pos") or {}
    lon = pos.get("x", 0) / 1e6
    lat = pos.get("y", 0) / 1e6
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None

    prod_l = common.get("prodL") or []
    prod = prod_l[jny.get("prodX", -1)] if 0 <= jny.get("prodX", -1) < len(prod_l) else {}
    prod_ctx = prod.get("prodCtx") or {}
    line = (prod_ctx.get("line") or prod.get("name") or "?").strip()
    category = (prod_ctx.get("catOutL") or prod.get("catOut") or "").strip()
    cls = prod.get("cls") or 0

    # next stop + delay from stopL (scheduled dTimeS vs realtime dTimeR)
    loc_l = common.get("locL") or []
    next_stop = None
    delay_s = None
    for st in jny.get("stopL") or []:
        loc = loc_l[st.get("locX", -1)] if 0 <= st.get("locX", -1) < len(loc_l) else {}
        if st.get("aTimeR") and st.get("aTimeS"):
            r = _hhmmss_to_sec(st["aTimeR"])
            s = _hhmmss_to_sec(st["aTimeS"])
            if r is not None and s is not None:
                delay_s = r - s
        if st.get("dTimeR") and st.get("dTimeS") and delay_s is None:
            r = _hhmmss_to_sec(st["dTimeR"])
            s = _hhmmss_to_sec(st["dTimeS"])
            if r is not None and s is not None:
                delay_s = r - s
        if loc.get("name"):
            next_stop = loc["name"]
            break

    # polyline of the vehicle's current segment (ani.polyG.polyXL -> polyL)
    polyline: list[dict[str, float]] = []
    poly_l = common.get("polyL") or []
    ani = jny.get("ani") or {}
    poly_g = ani.get("polyG") or {}
    for idx in poly_g.get("polyXL") or []:
        if 0 <= idx < len(poly_l):
            enc = poly_l[idx].get("crdEncYX") or ""
            if enc:
                polyline = [{"lat": la, "lon": lo} for la, lo in decode_polyline(enc)]
                break
    if len(polyline) > config.MAX_POLYLINE_POINTS:
        step = len(polyline) / config.MAX_POLYLINE_POINTS
        polyline = [polyline[int(i * step)] for i in range(config.MAX_POLYLINE_POINTS)]

    return {
        "id": str(jny.get("jid") or f"{lat:.5f},{lon:.5f}"),
        "lat": lat,
        "lon": lon,
        "line": line,
        "name": category,
        "destination": jny.get("dirTxt") or "",
        "category": category,
        "cls": cls,
        "mode": _mode_from_cls(cls),
        "delay_s": delay_s,
        "next_stop": next_stop,
        "polyline": polyline,
    }
