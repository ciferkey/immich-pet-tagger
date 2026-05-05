"""
Main entrypoint for immich-pet-tagger.
Starts the FastAPI enrollment UI and the background polling loop.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from poller import run_poll_cycle
from api import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))
DATA_DIR = os.environ.get("DATA_DIR", "/data")


async def polling_loop():
    log.info(f"Poller started. Interval: {POLL_INTERVAL}s. Data dir: {DATA_DIR}")
    while True:
        try:
            log.info("Starting poll cycle...")
            await asyncio.to_thread(run_poll_cycle, DATA_DIR)
            log.info("Poll cycle complete.")
        except Exception as e:
            log.exception(f"Poll cycle failed: {e}")
        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(polling_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Immich Pet Tagger", lifespan=lifespan)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    return {
        "poll_interval": POLL_INTERVAL,
        "data_dir": DATA_DIR,
        "immich_url": os.environ.get("IMMICH_URL", "not set"),
    }


@app.get("/")
async def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
