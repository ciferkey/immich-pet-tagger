import asyncio
import threading

scan_lock: asyncio.Lock | None = None
scan_cancel: threading.Event = threading.Event()
scan_generation: int = 0
neg_progress: dict = {"current": 0, "total": 0, "running": False}
neg_request_id: int = 0
borderline_progress: dict = {"current": 0, "total": 0, "running": False}
borderline_request_id: int = 0
manual_scan_result: dict | None = None
scan_low_conf_assets: list = []


def init():
    global scan_lock
    scan_lock = asyncio.Lock()
