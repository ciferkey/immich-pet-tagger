"""Immich HTTP API helpers used by the poller and enrollment API."""

import os
from datetime import datetime, timezone
import requests

# ── Immich ───────────────────────────────────────────────────────────────────
IMMICH_BASE = os.environ.get("IMMICH_URL", os.environ.get("IMMICH_BASE", "http://immich-server:2283")).rstrip("/")
IMMICH_PHOTO_URL = f"{IMMICH_BASE}/photos"
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
THUMB_SIZE = 256




def fetch_assets_taken_after(taken_after_iso: str) -> list[tuple[str, str]]:
    """POST /api/search/metadata with takenAfter; return [(asset_id, fileCreatedAt_iso), ...] ascending.
    Paginates with page/size (max 1000) until no more results.
    """
    url = f"{IMMICH_BASE}/api/search/metadata"
    headers = {"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json"}
    out: list[tuple[str, str]] = []
    page = 1
    size = 1000
    while True:
        body = {
            "takenAfter": taken_after_iso,
            "page": page,
            "size": size,
            "order": "asc",
        }
        try:
            r = requests.post(url, json=body, headers=headers, timeout=30)
            if r.status_code != 200:
                preview = ""
                try:
                    preview = (r.text or "")[:300].replace("\n", " ").replace("\r", " ")
                except Exception:
                    preview = "<unavailable>"
                print(
                    f"[debug immich_apis] POST {url} status={r.status_code} "
                    f"takenAfter={taken_after_iso} page={page} size={size} preview={preview}"
                )
                break
            data = r.json()
            assets_block = data.get("assets") or {}
            items = (assets_block.get("items") if isinstance(assets_block, dict) else None) or data.get("items")
            if not isinstance(items, list):
                items = []
            total = (assets_block.get("total", 0) if isinstance(assets_block, dict) else None) or data.get("total", 0)
            for a in items:
                aid = a.get("id")
                ts = a.get("fileCreatedAt") or a.get("localDateTime") or ""
                if aid and ts:
                    out.append((str(aid).strip("\x00"), ts))
            if len(items) < size or len(out) >= total:
                break
            page += 1
        except Exception as e:
            print(f"[debug immich_apis] POST {url} failed (exception) takenAfter={taken_after_iso} page={page} size={size}")
            break
    return out


def fetch_asset_face_person_ids(asset_id: str) -> set[str]:
    """GET /api/faces?id=asset_id; return set of person IDs already assigned to this asset."""
    url = f"{IMMICH_BASE}/api/faces"
    try:
        r = requests.get(
            url,
            params={"id": asset_id},
            headers={"x-api-key": IMMICH_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return set()
        data = r.json()
        if not isinstance(data, list):
            return set()
        return {str(f.get("person", {}).get("id")) for f in data if f.get("person", {}).get("id")}
    except Exception:
        return set()