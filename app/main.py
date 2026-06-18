"""
Main entrypoint for immich-pet-tagger.
Starts the FastAPI enrollment UI and the background polling loop.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from pathlib import Path
from embedder import load_embed_cache
from poller import run_poll_cycle, migrate_ref_bboxes
from api import router as api_router

BASE_DIR = Path(__file__).resolve().parent
import state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
LONG_REQUEST_TIMEOUT = int(os.environ.get("LONG_REQUEST_TIMEOUT", 120))


async def polling_loop():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Poller started. Interval: {POLL_INTERVAL}s. Data dir: {DATA_DIR}. Device: {device}")
    while True:
        try:
            log.info("Starting poll cycle...")
            async with state.scan_lock:
                await asyncio.to_thread(run_poll_cycle, DATA_DIR, None, state.scan_cancel)
            log.info("Poll cycle complete.")
        except Exception as e:
            log.exception(f"Poll cycle failed: {e}")
        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.init()
    load_embed_cache(Path(DATA_DIR))
    await asyncio.to_thread(migrate_ref_bboxes, Path(DATA_DIR))
    task = asyncio.create_task(polling_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Immich Pet Tagger", lifespan=lifespan)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        timeout_keep_alive=LONG_REQUEST_TIMEOUT,
    )
