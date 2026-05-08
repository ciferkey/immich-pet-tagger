import asyncio

scan_lock: asyncio.Lock | None = None
neg_progress: dict = {"current": 0, "total": 0, "running": False}
neg_request_id: int = 0
borderline_progress: dict = {"current": 0, "total": 0, "running": False}
borderline_request_id: int = 0
manual_scan_result: dict | None = None


def init():
    global scan_lock
    scan_lock = asyncio.Lock()
