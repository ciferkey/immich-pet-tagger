"""
API routes for the enrollment UI.
All Immich communication happens here, the browser never touches Immich directly.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("api")

router = APIRouter(prefix="/api")

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server:2283")
IMMICH_EXTERNAL_URL = os.environ.get("IMMICH_EXTERNAL_URL", "http://localhost:2283")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
PETS_DIR = DATA_DIR / "pets"


def immich_headers() -> dict:
    return {"x-api-key": IMMICH_API_KEY, "Accept": "application/json"}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def load_pet_refs(pet_name: str) -> list[dict]:
    """Load refs as list of {asset_id, face_id}. Handles legacy list-of-strings format."""
    ref_file = PETS_DIR / pet_name / "refs.json"
    if not ref_file.exists():
        return []
    data = json.loads(ref_file.read_text(encoding="utf-8"))
    if not data:
        return []
    # Legacy format: list of strings
    if isinstance(data[0], str):
        return [{"asset_id": aid, "face_id": None} for aid in data]
    return data


def load_pet_asset_ids(pet_name: str) -> list[str]:
    """Return just asset IDs (for backward compat with poller and other callers)."""
    return [r["asset_id"] for r in load_pet_refs(pet_name)]


def save_pet_refs(pet_name: str, refs: list[dict]) -> None:
    pet_dir = PETS_DIR / pet_name
    pet_dir.mkdir(parents=True, exist_ok=True)
    (pet_dir / "refs.json").write_text(json.dumps(refs, indent=2), encoding="utf-8")


def save_pet_asset_ids(pet_name: str, asset_ids: list[str]) -> None:
    """Legacy wrapper, preserves existing face_ids when re-saving."""
    existing = {r["asset_id"]: r.get("face_id") for r in load_pet_refs(pet_name)}
    refs = [{"asset_id": aid, "face_id": existing.get(aid)} for aid in asset_ids]
    save_pet_refs(pet_name, refs)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PetCreate(BaseModel):
    name: str
    since: Optional[str] = None   # ISO date string e.g. "2023-01-01"
    until: Optional[str] = None


class PetUpdate(BaseModel):
    name: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None


class PetAssets(BaseModel):
    asset_ids: list[str]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_config():
    """Returns browser-safe configuration."""
    return {"immich_external_url": IMMICH_EXTERNAL_URL}


@router.get("/search")
async def search_assets(q: str, limit: int = 40, since: Optional[str] = None, until: Optional[str] = None):
    """Proxy smart search to Immich, optionally scoped by date range."""
    body: dict = {"query": q, "type": "IMAGE", "limit": limit}
    if since:
        body["takenAfter"] = since + "T00:00:00.000Z"
    if until:
        body["takenBefore"] = until + "T23:59:59.999Z"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{IMMICH_URL}/api/search/smart",
            headers=immich_headers(),
            json=body,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    assets = data.get("assets", {}).get("items", [])
    return {"assets": [_slim_asset(a) for a in assets]}


def _slim_asset(a: dict) -> dict:
    """Return only what the UI needs."""
    return {
        "id": a["id"],
        "thumb": f"/api/thumb/{a['id']}",
        "date": a.get("localDateTime", "")[:10],
        "filename": a.get("originalFileName", ""),
    }


# ---------------------------------------------------------------------------
# Pets
# ---------------------------------------------------------------------------

@router.get("/pets")
async def list_pets():
    config = load_config()
    result = []
    for name, cfg in config.items():
        result.append({
            "name": name,
            "person_id": cfg.get("person_id"),
            "since": cfg.get("since"),
            "until": cfg.get("until"),
            "ref_count": len(load_pet_asset_ids(name)),
        })
    return {"pets": result}


@router.post("/pets")
async def create_pet(pet: PetCreate):
    config = load_config()
    name = pet.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if any(c in name for c in r'/\.'):
        raise HTTPException(status_code=400, detail="Pet name cannot contain /, \\, or .")
    if name.lower() in {k.lower() for k in config}:
        raise HTTPException(status_code=409, detail=f"Pet '{name}' already exists")

    # Create person in Immich
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{IMMICH_URL}/api/people",
            headers=immich_headers(),
            json={"name": name},
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=f"Immich error: {resp.text}")

    person_id = resp.json().get("id")
    config[name] = {
        "person_id": person_id,
        "since": pet.since,
        "until": pet.until,
    }
    save_config(config)
    (PETS_DIR / name).mkdir(parents=True, exist_ok=True)
    log.info(f"Created pet '{name}' with person_id={person_id}")
    return {"name": name, "person_id": person_id}


@router.patch("/pets/{name}")
async def update_pet(name: str, update: PetUpdate):
    config = load_config()
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    new_name = update.name.strip() if update.name else None
    if new_name and new_name != name:
        if any(c in new_name for c in r'/\.'):
            raise HTTPException(status_code=400, detail="Pet name cannot contain /, \\, or .")
        if new_name.lower() in {k.lower() for k in config if k != name}:
            raise HTTPException(status_code=409, detail=f"Pet '{new_name}' already exists")
        # Rename in Immich
        person_id = config[name].get("person_id")
        if person_id:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.put(
                    f"{IMMICH_URL}/api/people/{person_id}",
                    headers=immich_headers(),
                    json={"name": new_name},
                )
        # Rename folder on disk
        old_dir = PETS_DIR / name
        new_dir = PETS_DIR / new_name
        if old_dir.exists():
            old_dir.rename(new_dir)
        # Rename key in config
        config[new_name] = config.pop(name)
        name = new_name

    if "since" in update.model_fields_set:
        config[name]["since"] = update.since
    if "until" in update.model_fields_set:
        config[name]["until"] = update.until
    save_config(config)
    log.info(f"Updated pet '{name}'")
    return {"ok": True}


@router.delete("/pets/{name}")
async def delete_pet(name: str):
    config = load_config()
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")

    if person_id:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: delete stored face_ids for all refs
            refs = load_pet_refs(name)
            for ref in refs:
                face_id = ref.get("face_id")
                asset_id = ref.get("asset_id")
                if face_id:
                    resp_face = await client.request(
                        "DELETE",
                        f"{IMMICH_URL}/api/faces/{face_id}",
                        headers=immich_headers(),
                        json={"force": True},
                    )
                    log.info(f"Deleted face {face_id} on asset {asset_id} (status={resp_face.status_code})")
                else:
                    log.warning(f"No stored face_id for asset {asset_id}, skipping face deletion")

            # Step 2: delete the Immich person
            resp = await client.delete(
                f"{IMMICH_URL}/api/people/{person_id}",
                headers=immich_headers(),
            )
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=resp.status_code, detail=f"Immich error: {resp.text}")
        log.info(f"Deleted Immich person {person_id} for pet '{name}'")

    del config[name]
    save_config(config)

    # Remove pet folder from disk
    pet_dir = PETS_DIR / name
    if pet_dir.exists():
        shutil.rmtree(pet_dir)
        log.info(f"Removed pet directory {pet_dir}")

    log.info(f"Deleted pet '{name}' from config")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Negatives (unknown / not a pet)
# ---------------------------------------------------------------------------

NEGATIVES_FILE = DATA_DIR / "negatives.json"


def load_negative_ids() -> list[str]:
    if NEGATIVES_FILE.exists():
        return json.loads(NEGATIVES_FILE.read_text(encoding="utf-8"))
    return []


def save_negative_ids(ids: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NEGATIVES_FILE.write_text(json.dumps(ids, indent=2), encoding="utf-8")


@router.get("/negatives")
async def get_negatives():
    ids = load_negative_ids()
    assets = [{"id": aid, "thumb": f"/api/thumb/{aid}"} for aid in ids]
    return {"assets": assets, "count": len(ids)}


@router.post("/negatives")
async def add_negatives(body: PetAssets):
    existing = set(load_negative_ids())
    merged = list(existing | set(body.asset_ids))
    save_negative_ids(merged)
    log.info(f"Negatives: {len(merged)} total (+{len(set(body.asset_ids) - existing)} new)")
    return {"ok": True, "count": len(merged)}


@router.delete("/negatives/{asset_id}")
async def remove_negative(asset_id: str):
    ids = [i for i in load_negative_ids() if i != asset_id]
    save_negative_ids(ids)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pet reference assets
# ---------------------------------------------------------------------------

@router.get("/pets/{name}/assets")
async def get_pet_assets(name: str):
    config = load_config()
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    asset_ids = load_pet_asset_ids(name)
    assets = [{"id": aid, "thumb": f"/api/thumb/{aid}"} for aid in asset_ids]
    return {"assets": assets}


FACE_BOX_SIZE = 256


async def post_face(client: httpx.AsyncClient, asset_id: str, person_id: str) -> str | None:
    """Create a face entry in Immich. Returns face_id on success, None on failure.
    Immich returns 201 with empty body, so we fetch the face_id via GET after creation."""
    try:
        resp = await client.post(
            f"{IMMICH_URL}/api/faces",
            headers={**immich_headers(), "Content-Type": "application/json"},
            json={
                "assetId": asset_id,
                "personId": person_id,
                "width": FACE_BOX_SIZE,
                "height": FACE_BOX_SIZE,
                "imageWidth": FACE_BOX_SIZE,
                "imageHeight": FACE_BOX_SIZE,
                "x": 0,
                "y": 0,
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            log.warning(f"post_face failed {resp.status_code}: {resp.text[:200]}")
            return None
        # Fetch face_id via GET since POST returns empty body
        faces_resp = await client.get(
            f"{IMMICH_URL}/api/faces",
            headers=immich_headers(),
            params={"id": asset_id},
        )
        if faces_resp.status_code == 200:
            for face in faces_resp.json():
                if face.get("person", {}).get("id") == person_id:
                    return face.get("id")
        log.warning(f"post_face: created but could not retrieve face_id for asset {asset_id}")
        return None
    except Exception as e:
        log.error(f"post_face error: {e}")
        return None


async def get_existing_face_person_ids(client: httpx.AsyncClient, asset_id: str) -> set[str]:
    """Return set of person_ids already assigned to this asset."""
    try:
        resp = await client.get(
            f"{IMMICH_URL}/api/faces",
            headers=immich_headers(),
            params={"id": asset_id},
            timeout=15,
        )
        if resp.status_code == 200:
            return {f.get("person", {}).get("id") for f in resp.json() if f.get("person")}
    except Exception as e:
        log.warning(f"get_existing_face_person_ids error: {e}")
    return set()


@router.post("/pets/{name}/assets")
async def set_pet_assets(name: str, body: PetAssets):
    config = load_config()
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")

    existing_ids = set(load_pet_asset_ids(name))
    new_ids = [aid for aid in body.asset_ids if aid not in existing_ids]
    log.info(f"Saving {len(body.asset_ids)} refs for pet '{name}' ({len(new_ids)} new)")

    # Assign faces in Immich for newly added assets, storing returned face_id
    ok = fail = skipped = 0
    existing_refs = {r["asset_id"]: r.get("face_id") for r in load_pet_refs(name)}

    if person_id and new_ids:
        async with httpx.AsyncClient(timeout=30) as client:
            for aid in new_ids:
                existing_persons = await get_existing_face_person_ids(client, aid)
                if person_id in existing_persons:
                    skipped += 1
                    continue
                face_id = await post_face(client, aid, person_id)
                if face_id:
                    existing_refs[aid] = face_id
                    ok += 1
                else:
                    fail += 1
        log.info(f"Face assignment for '{name}': {ok} ok, {fail} failed, {skipped} already present")
    elif not person_id:
        log.warning(f"Pet '{name}' has no person_id, skipping face assignment")

    # Save refs with face_ids
    final_refs = [{"asset_id": aid, "face_id": existing_refs.get(aid)} for aid in body.asset_ids]
    save_pet_refs(name, final_refs)
    return {"ok": True, "count": len(body.asset_ids), "faces_added": ok, "faces_failed": fail}


@router.delete("/pets/{name}/assets/{asset_id}")
async def remove_pet_asset(name: str, asset_id: str):
    config = load_config()
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    refs = load_pet_refs(name)
    ref = next((r for r in refs if r["asset_id"] == asset_id), None)
    face_id = ref.get("face_id") if ref else None

    if face_id:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(
                "DELETE",
                f"{IMMICH_URL}/api/faces/{face_id}",
                headers=immich_headers(),
                json={"force": True},
            )
            log.info(f"Deleted face {face_id} on asset {asset_id} for pet '{name}' (status={resp.status_code})")
    else:
        log.warning(f"No stored face_id for asset {asset_id} on pet '{name}' — face not removed from Immich")

    updated = [r for r in refs if r["asset_id"] != asset_id]
    save_pet_refs(name, updated)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Scan timestamp
# ---------------------------------------------------------------------------

STATE_FILE = DATA_DIR / "last_scan_timestamp.txt"

@router.get("/poll-status")
async def get_poll_status():
    path = DATA_DIR / "last_poll_status.json"
    if not path.exists():
        return {"status": "never"}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/timestamp")
async def get_timestamp():
    if STATE_FILE.exists():
        val = STATE_FILE.read_text(encoding="utf-8").strip()
    else:
        val = ""
    return {"timestamp": val}


class TimestampBody(BaseModel):
    date: str  # YYYY-MM-DD

@router.post("/timestamp")
async def set_timestamp(body: TimestampBody):
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.date):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    ts = body.date + "T00:00:00.000Z"
    STATE_FILE.write_text(ts + "\n", encoding="utf-8")
    log.info(f"Scan timestamp reset to {ts}")
    return {"timestamp": ts}


# ---------------------------------------------------------------------------
# Thumbnail proxy
# ---------------------------------------------------------------------------


@router.get("/person-thumb/{person_id}")
async def person_thumbnail(person_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{IMMICH_URL}/api/people/{person_id}/thumbnail",
            headers=immich_headers(),
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code)
    return StreamingResponse(resp.aiter_bytes(), media_type=resp.headers.get("content-type", "image/jpeg"))


@router.get("/thumb/{asset_id}")
async def thumbnail(asset_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview",
            headers=immich_headers(),
        )
    return StreamingResponse(
        resp.aiter_bytes(),
        media_type=resp.headers.get("content-type", "image/jpeg"),
    )
