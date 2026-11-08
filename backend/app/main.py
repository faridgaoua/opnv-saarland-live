"""FastAPI app: REST + SSE + serves the Leaflet frontend."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.start()
    yield
    await store.stop()


app = FastAPI(title="Saarland Live Train Tracker", lifespan=lifespan)


@app.get("/api/vehicles")
async def vehicles():
    return store.snapshot()


@app.get("/api/stops")
async def stops():
    from . import gtfs_static

    return gtfs_static.stops()


@app.get("/api/routes")
async def routes():
    from . import gtfs_static

    return gtfs_static.routes()


async def _event_stream():
    q = await store.subscribe()
    try:
        # send current state immediately
        yield f"data: {json.dumps(store.snapshot())}\n\n"
        while True:
            try:
                snap = await asyncio.wait_for(q.get(), timeout=25.0)
                yield f"data: {json.dumps(snap)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        store.unsubscribe(q)


@app.get("/api/stream")
async def stream():
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")
