"""Central configuration: endpoints, auth, filters, intervals."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- HAFAS (saarfahrplan / ZPS Saarland) ---------------------------------
HAFAS_ENDPOINT = "https://saarfahrplan.de/bin/mgate.exe"
HAFAS_AID = "51XfsVqgbdA6oXzHrx75jhlocRg6Xe"
HAFAS_CLIENT = {"type": "AND", "id": "ZPS-SAAR", "v": 1000070, "name": "Saarfahrplan"}
HAFAS_VER = "1.40"
HAFAS_LANG = "de"
HAFAS_SALT = b"HJtlubisvxiJxss"
HAFAS_USER_AGENT = "saarland-live-tracker/0.1 (local dev project)"

# Product bitmask for this endpoint (verified empirically against
# JourneyGeoPos): cls 4/128 = bus, 8 = regional rail, 16 = S-Bahn,
# 32/64/256 = tram/subway-ish, 512 = Anruf-Sammel-Taxi.
# (The hafas-client profile bitmasks do NOT match this endpoint's classes.)
TRANSIT_MASK = 4 + 8 + 16 + 32 + 64 + 128 + 256 + 512  # 1020

# --- GTFS-RT (gtfs.de free stream, includes SIRI Saarland) ---------------
# Rail-only (13.7 MB / 10 s). Off by default: delay stats come from the
# HAFAS radar, which covers buses too.
GTFS_RT_URL = "https://realtime.gtfs.de/realtime-free.pb"
ENABLE_GTFS_RT = False

# --- Static GTFS (gtfs.de, CC BY 4.0, rolling 30 days) -------------------
GTFS_FEEDS = {
    "de_rv": "https://download.gtfs.de/germany/rv_free/latest.zip",
    "de_fv": "https://download.gtfs.de/germany/fv_free/latest.zip",
}

# --- Geography: Saarland bounding box (min_lat, min_lon, max_lat, max_lon)
SAARLAND_BBOX = (49.10, 6.00, 49.70, 7.60)

# --- Refresh intervals (seconds) -----------------------------------------
RADAR_INTERVAL = 10
GTFS_RT_INTERVAL = 10

# Cap polyline points per vehicle (keeps SSE snapshots small)
MAX_POLYLINE_POINTS = 60
