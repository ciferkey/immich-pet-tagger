"""Immich HTTP helpers. Sync functions are used by the poller (runs in a thread).
Async functions are used by the API routes."""

import logging
import os

import httpx
import requests

log = logging.getLogger("immich")

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server:2283").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")

FACE_BOX_SIZE = 256


def headers() -> dict:
    return {"x-api-key": IMMICH_API_KEY, "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Sync (poller)
# ---------------------------------------------------------------------------

def fetch_assets_taken_after(taken_after_iso: str) -> list[tuple[str, str]]:
    """Return [(asset_id, fileCreatedAt_iso), ...] for all assets taken after the given timestamp."""
    url = f"{IMMICH_URL}/api/search/metadata"
    hdrs = {**headers(), "Content-Type": "application/json"}
    out: list[tuple[str, str]] = []
    page = 1
    size = 1000
    while True:
        try:
            r = requests.post(url, json={"takenAfter": taken_after_iso, "page": page, "size": size, "order": "asc"}, headers=hdrs, timeout=30)
            if r.status_code != 200:
                log.warning(f"fetch_assets_taken_after: status={r.status_code} page={page}")
                break
            data = r.json()
            block = data.get("assets") or {}
            items = (block.get("items") if isinstance(block, dict) else None) or data.get("items") or []
            total = (block.get("total", 0) if isinstance(block, dict) else 0) or data.get("total", 0)
            for a in items:
                aid = a.get("id")
                ts = a.get("fileCreatedAt") or a.get("localDateTime") or ""
                if aid and ts:
                    out.append((str(aid).strip("\x00"), ts))
            if len(items) < size:
                break
            page += 1
        except Exception as e:
            log.error(f"fetch_assets_taken_after error: {e}")
            break
    return out


def fetch_asset_face_person_ids(asset_id: str) -> set[str]:
    """Return set of person_ids already assigned as faces on this asset."""
    try:
        r = requests.get(f"{IMMICH_URL}/api/faces", params={"id": asset_id}, headers=headers(), timeout=10)
        if r.status_code != 200 or not isinstance(r.json(), list):
            return set()
        return {str(f["person"]["id"]) for f in r.json() if (f.get("person") or {}).get("id")}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Async (API routes)
# ---------------------------------------------------------------------------

async def post_face(client: httpx.AsyncClient, asset_id: str, person_id: str) -> str | None:
    """Create a face entry in Immich. Returns face_id on success, None on failure.
    Immich returns 201 with empty body, so face_id is fetched via GET after creation."""
    try:
        resp = await client.post(
            f"{IMMICH_URL}/api/faces",
            headers={**headers(), "Content-Type": "application/json"},
            json={"assetId": asset_id, "personId": person_id,
                  "width": FACE_BOX_SIZE, "height": FACE_BOX_SIZE,
                  "imageWidth": FACE_BOX_SIZE, "imageHeight": FACE_BOX_SIZE,
                  "x": 0, "y": 0},
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            log.warning(f"post_face failed {resp.status_code}: {resp.text[:200]}")
            return None
        faces_resp = await client.get(f"{IMMICH_URL}/api/faces", headers=headers(), params={"id": asset_id})
        if faces_resp.status_code == 200:
            for face in faces_resp.json():
                if (face.get("person") or {}).get("id") == person_id:
                    return face.get("id")
        log.warning(f"post_face: created but could not retrieve face_id for asset {asset_id}")
        return None
    except Exception as e:
        log.error(f"post_face error: {e}")
        return None


async def get_existing_face_person_ids(client: httpx.AsyncClient, asset_id: str) -> set[str]:
    """Return set of person_ids already assigned as faces on this asset (async)."""
    try:
        resp = await client.get(f"{IMMICH_URL}/api/faces", headers=headers(), params={"id": asset_id}, timeout=15)
        if resp.status_code == 200:
            return {f.get("person", {}).get("id") for f in resp.json() if f.get("person")}
    except Exception as e:
        log.warning(f"get_existing_face_person_ids error: {e}")
    return set()
