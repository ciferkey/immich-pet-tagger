"""API routes for the enrollment UI.
All Immich communication happens here; the browser never touches Immich directly."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import data
import immich as imm
import state
from poller import embed_asset

log = logging.getLogger("api")

router = APIRouter(prefix="/api")

IMMICH_EXTERNAL_URL = os.environ.get("IMMICH_EXTERNAL_URL", "http://localhost:2283")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PETS_DIR = DATA_DIR / "pets"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PetCreate(BaseModel):
    name: str
    since: Optional[str] = None
    until: Optional[str] = None
    description: str


class PetUpdate(BaseModel):
    name: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    description: Optional[str] = None


class PetAssets(BaseModel):
    asset_ids: list[str]


@router.get("/config")
async def get_config():
    return {"immich_external_url": IMMICH_EXTERNAL_URL}


def _slim_asset(a: dict) -> dict:
    return {"id": a["id"], "thumb": f"/api/thumb/{a['id']}", "date": a.get("localDateTime", "")[:10], "filename": a.get("originalFileName", "")}


async def _visual_search(
    client: httpx.AsyncClient,
    ref_ids: list[str],
    pet_cfg: dict,
    exclude: set[str],
    sample: int = 8,
    per_ref_limit: int = 50,
) -> list[dict]:
    """Query Immich smart search using ref asset IDs instead of text.
    Runs all ref queries in parallel and returns deduplicated candidates."""
    if len(ref_ids) > sample:
        step = len(ref_ids) / sample
        sampled = [ref_ids[int(i * step)] for i in range(sample)]
    else:
        sampled = ref_ids

    base: dict = {"type": "IMAGE", "limit": per_ref_limit}
    if pet_cfg.get("since"):
        base["takenAfter"] = pet_cfg["since"] + "T00:00:00.000Z"
    if pet_cfg.get("until"):
        base["takenBefore"] = pet_cfg["until"] + "T23:59:59.999Z"

    async def fetch_one(rid: str) -> list[dict]:
        try:
            resp = await client.post(
                f"{imm.IMMICH_URL}/api/search/smart",
                headers=imm.headers(),
                json={**base, "queryAssetId": rid},
            )
            if resp.status_code == 200:
                return resp.json().get("assets", {}).get("items", [])
        except Exception:
            pass
        return []

    results = await asyncio.gather(*[fetch_one(rid) for rid in sampled])
    seen: set[str] = set()
    candidates: list[dict] = []
    for items in results:
        for a in items:
            aid = a.get("id")
            if aid and aid not in exclude and aid not in seen:
                seen.add(aid)
                candidates.append(a)
    return candidates


# ---------------------------------------------------------------------------
# Pets
# ---------------------------------------------------------------------------

@router.get("/pets")
async def list_pets():
    config = data.load_config(DATA_DIR)
    return {"pets": [
        {"name": name, "person_id": cfg.get("person_id"), "since": cfg.get("since"),
         "until": cfg.get("until"), "description": cfg.get("description"),
         "ref_count": len(data.load_pet_asset_ids(name, DATA_DIR))}
        for name, cfg in config.items()
    ]}


@router.post("/pets")
async def create_pet(pet: PetCreate):
    config = data.load_config(DATA_DIR)
    name = pet.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if any(c in name for c in r'/\.'):
        raise HTTPException(status_code=400, detail="Pet name cannot contain /, \\, or .")
    if name.lower() in {k.lower() for k in config}:
        raise HTTPException(status_code=409, detail=f"Pet '{name}' already exists")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{imm.IMMICH_URL}/api/people", headers=imm.headers(), json={"name": name})
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=f"Immich error: {resp.text}")

    person_id = resp.json().get("id")
    config[name] = {"person_id": person_id, "since": pet.since, "until": pet.until, "description": pet.description}
    data.save_config(config, DATA_DIR)
    (PETS_DIR / name).mkdir(parents=True, exist_ok=True)
    log.info(f"Created pet '{name}' with person_id={person_id}")
    return {"name": name, "person_id": person_id}


@router.patch("/pets/{name}")
async def update_pet(name: str, update: PetUpdate):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    new_name = update.name.strip() if update.name else None
    if new_name and new_name != name:
        if any(c in new_name for c in r'/\.'):
            raise HTTPException(status_code=400, detail="Pet name cannot contain /, \\, or .")
        if new_name.lower() in {k.lower() for k in config if k != name}:
            raise HTTPException(status_code=409, detail=f"Pet '{new_name}' already exists")
        person_id = config[name].get("person_id")
        if person_id:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.put(f"{imm.IMMICH_URL}/api/people/{person_id}", headers=imm.headers(), json={"name": new_name})
        old_dir = PETS_DIR / name
        if old_dir.exists():
            old_dir.rename(PETS_DIR / new_name)
        config[new_name] = config.pop(name)
        name = new_name

    if "since" in update.model_fields_set:
        config[name]["since"] = update.since
    if "until" in update.model_fields_set:
        config[name]["until"] = update.until
    if "description" in update.model_fields_set:
        config[name]["description"] = update.description
    data.save_config(config, DATA_DIR)
    log.info(f"Updated pet '{name}'")
    return {"ok": True}


@router.delete("/pets/{name}")
async def delete_pet(name: str, local_only: bool = False):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")

    if not local_only and person_id:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(f"{imm.IMMICH_URL}/api/people/{person_id}", headers=imm.headers())
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=resp.status_code, detail=f"Immich error: {resp.text}")
        log.info(f"Deleted Immich person {person_id} for pet '{name}', face cleanup running in background")

    del config[name]
    data.save_config(config, DATA_DIR)
    pet_dir = PETS_DIR / name
    if pet_dir.exists():
        shutil.rmtree(pet_dir)
    log.info(f"Deleted pet '{name}' (local_only={local_only})")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Negatives
# ---------------------------------------------------------------------------

@router.get("/negatives")
async def get_negatives():
    ids = data.load_negative_ids(DATA_DIR)
    return {"assets": [{"id": aid, "thumb": f"/api/thumb/{aid}"} for aid in ids], "count": len(ids)}


@router.post("/negatives")
async def add_negatives(body: PetAssets):
    existing = set(data.load_negative_ids(DATA_DIR))
    merged = list(existing | set(body.asset_ids))
    data.save_negative_ids(merged, DATA_DIR)
    log.info(f"Negatives: {len(merged)} total (+{len(set(body.asset_ids) - existing)} new)")
    return {"ok": True, "count": len(merged)}


@router.delete("/pets/{name}/refs")
async def clear_pet_refs(name: str):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    data.save_pet_refs(name, [], DATA_DIR)
    log.info(f"Cleared all refs for pet '{name}' (local only)")
    return {"ok": True}


@router.delete("/negatives/all")
async def clear_all_negatives():
    data.save_negative_ids([], DATA_DIR)
    log.info("Cleared all negatives (local only)")
    return {"ok": True}


@router.delete("/negatives/{asset_id}")
async def remove_negative(asset_id: str):
    ids = [i for i in data.load_negative_ids(DATA_DIR) if i != asset_id]
    data.save_negative_ids(ids, DATA_DIR)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Skipped
# ---------------------------------------------------------------------------

@router.post("/skipped")
async def add_skipped(body: PetAssets):
    existing = set(data.load_skipped_ids(DATA_DIR))
    merged = list(existing | set(body.asset_ids))
    data.save_skipped_ids(merged, DATA_DIR)
    return {"count": len(merged)}


# ---------------------------------------------------------------------------
# Pet reference assets
# ---------------------------------------------------------------------------

@router.get("/pets/{name}/assets")
async def get_pet_assets(name: str):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    asset_ids = data.load_pet_asset_ids(name, DATA_DIR)
    return {"assets": [{"id": aid, "thumb": f"/api/thumb/{aid}"} for aid in asset_ids]}


@router.post("/pets/{name}/assets")
async def set_pet_assets(name: str, body: PetAssets):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")

    existing_ids = set(data.load_pet_asset_ids(name, DATA_DIR))
    new_ids = [aid for aid in body.asset_ids if aid not in existing_ids]
    log.info(f"Saving {len(body.asset_ids)} refs for pet '{name}' ({len(new_ids)} new)")

    ok = fail = skipped = 0
    existing_refs = {r["asset_id"]: r.get("face_id") for r in data.load_pet_refs(name, DATA_DIR)}

    if person_id and new_ids:
        async with httpx.AsyncClient(timeout=30) as client:
            for aid in new_ids:
                existing_persons = await imm.get_existing_face_person_ids(client, aid)
                if person_id in existing_persons:
                    skipped += 1
                    continue
                face_id = await imm.post_face(client, aid, person_id)
                if face_id:
                    existing_refs[aid] = face_id
                    ok += 1
                else:
                    fail += 1
        log.info(f"Face assignment for '{name}': {ok} ok, {fail} failed, {skipped} already present")
    elif not person_id:
        log.warning(f"Pet '{name}' has no person_id, skipping face assignment")

    final_refs = [{"asset_id": aid, "face_id": existing_refs.get(aid)} for aid in body.asset_ids]
    data.save_pet_refs(name, final_refs, DATA_DIR)
    return {"ok": True, "count": len(body.asset_ids), "faces_added": ok, "faces_failed": fail}


@router.delete("/pets/{name}/assets/{asset_id}")
async def remove_pet_asset(name: str, asset_id: str):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    refs = data.load_pet_refs(name, DATA_DIR)
    ref = next((r for r in refs if r["asset_id"] == asset_id), None)
    face_id = ref.get("face_id") if ref else None

    if face_id:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request("DELETE", f"{imm.IMMICH_URL}/api/faces/{face_id}", headers=imm.headers(), json={"force": True})
        log.info(f"Deleted face {face_id} on asset {asset_id} for pet '{name}' (status={resp.status_code})")
    else:
        log.warning(f"No stored face_id for asset {asset_id} on pet '{name}', face not removed from Immich")

    data.save_pet_refs(name, [r for r in refs if r["asset_id"] != asset_id], DATA_DIR)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tagged assets
# ---------------------------------------------------------------------------

@router.get("/pets/{name}/tagged")
async def get_tagged_assets(name: str):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")
    if not person_id:
        return {"assets": [], "count": 0}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{imm.IMMICH_URL}/api/search/metadata", headers=imm.headers(), json={"personIds": [person_id], "type": "IMAGE", "size": 1000})
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    assets = resp.json().get("assets", {}).get("items", [])
    return {"assets": [_slim_asset(a) for a in assets], "count": len(assets)}


@router.post("/pets/{name}/reject")
async def reject_tagged_assets(name: str, body: PetAssets):
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")
    person_id = config[name].get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Pet has no person_id")

    removed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for asset_id in body.asset_ids:
            faces_resp = await client.get(f"{imm.IMMICH_URL}/api/faces", headers=imm.headers(), params={"id": asset_id})
            if faces_resp.status_code == 200:
                for face in faces_resp.json():
                    if (face.get("person") or {}).get("id") == person_id:
                        await client.request("DELETE", f"{imm.IMMICH_URL}/api/faces/{face.get('id')}", headers=imm.headers(), json={"force": True})
                        removed += 1
                        break

    existing = set(data.load_negative_ids(DATA_DIR))
    merged = list(existing | set(body.asset_ids))
    data.save_negative_ids(merged, DATA_DIR)
    log.info(f"Rejected {len(body.asset_ids)} assets for '{name}': {removed} faces removed, {len(merged)-len(existing)} added to negatives")
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# Ref suggestions
# ---------------------------------------------------------------------------

@router.get("/pets/{name}/suggestions")
async def get_suggestions(name: str, limit: int = 20):
    from poller import build_classifier
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    pet_cfg = config[name]
    description = pet_cfg.get("description", "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="no_description")

    ref_ids = data.load_pet_asset_ids(name, DATA_DIR)
    ref_set = set(ref_ids)
    neg_ids = set(data.load_negative_ids(DATA_DIR))
    exclude = ref_set | neg_ids

    async with httpx.AsyncClient(timeout=30) as client:
        if ref_ids:
            # Stage 1: visual search using ref photos as queries
            candidates = await _visual_search(client, ref_ids, pet_cfg, exclude)
        else:
            # Fallback for 0-ref case: text search
            body: dict = {"query": description, "type": "IMAGE", "limit": 60}
            if pet_cfg.get("since"):
                body["takenAfter"] = pet_cfg["since"] + "T00:00:00.000Z"
            if pet_cfg.get("until"):
                body["takenBefore"] = pet_cfg["until"] + "T23:59:59.999Z"
            resp = await client.post(f"{imm.IMMICH_URL}/api/search/smart", headers=imm.headers(), json=body)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            all_items = resp.json().get("assets", {}).get("items", [])
            candidates = [a for a in all_items if a["id"] not in exclude]

    if not candidates:
        return {"assets": []}

    if not ref_ids:
        return {"assets": [_slim_asset(a) for a in candidates[:limit]]}

    # Stage 2: classify candidates with the same classifier as the poller
    all_pet_names = list(config.keys())
    all_ref_ids = {n: data.load_pet_asset_ids(n, DATA_DIR) for n in all_pet_names}
    pet_names = [n for n in all_pet_names if all_ref_ids.get(n)]
    ref_ids_per_pet = {n: all_ref_ids[n] for n in pet_names}
    negative_ids = data.load_negative_ids(DATA_DIR)

    def compute():
        result = build_classifier(pet_names, ref_ids_per_pet, negative_ids)
        if result is None:
            return candidates[:limit]
        names, clf, scaler = result
        if name not in names:
            return candidates[:limit]
        pet_idx = names.index(name)
        scored = []
        for a in candidates:
            vec = embed_asset(a["id"])
            if vec is not None:
                v = np.asarray(vec, dtype=np.float64).reshape(1, -1)
                pet_prob = float(clf.predict_proba(scaler.transform(v))[0][pet_idx])
                scored.append((pet_prob, a))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[:limit]]

    results = await asyncio.to_thread(compute)
    return {"assets": [_slim_asset(a) for a in results]}


@router.get("/pets/{name}/borderline")
async def get_borderline(name: str, limit: int = 40):
    from poller import build_classifier
    config = data.load_config(DATA_DIR)
    if name not in config:
        raise HTTPException(status_code=404, detail=f"Pet '{name}' not found")

    pet_cfg = config[name]
    ref_ids = data.load_pet_asset_ids(name, DATA_DIR)
    if not ref_ids:
        raise HTTPException(status_code=400, detail="no_refs")

    ref_set = set(ref_ids)
    neg_ids = set(data.load_negative_ids(DATA_DIR))
    skipped_ids = set(data.load_skipped_ids(DATA_DIR))
    exclude = ref_set | neg_ids | skipped_ids

    async with httpx.AsyncClient(timeout=30) as client:
        candidates = await _visual_search(client, ref_ids, pet_cfg, exclude)

    if not candidates:
        return {"assets": []}

    all_pet_names = list(config.keys())
    all_ref_ids = {n: data.load_pet_asset_ids(n, DATA_DIR) for n in all_pet_names}
    pet_names = [n for n in all_pet_names if all_ref_ids.get(n)]
    ref_ids_per_pet = {n: all_ref_ids[n] for n in pet_names}
    negative_ids = data.load_negative_ids(DATA_DIR)

    LOW, HIGH = 0.3, 0.85

    state.borderline_request_id += 1
    my_id = state.borderline_request_id

    def compute():
        state.borderline_progress["current"] = 0
        state.borderline_progress["total"] = 0
        state.borderline_progress["running"] = True
        try:
            result = build_classifier(pet_names, ref_ids_per_pet, negative_ids)
            if result is None:
                return []
            names, clf, scaler = result
            if name not in names:
                return []
            pet_idx = names.index(name)
            state.borderline_progress["total"] = len(candidates)
            scored = []
            for i, a in enumerate(candidates):
                if state.borderline_request_id != my_id:
                    return []
                state.borderline_progress["current"] = i + 1
                vec = embed_asset(a["id"])
                if vec is not None:
                    v = np.asarray(vec, dtype=np.float64).reshape(1, -1)
                    pet_prob = float(clf.predict_proba(scaler.transform(v))[0][pet_idx])
                    if LOW <= pet_prob < HIGH:
                        scored.append((pet_prob, a))
            scored.sort(key=lambda x: x[0])
            return scored[:limit]
        finally:
            if state.borderline_request_id == my_id:
                state.borderline_progress["running"] = False

    from poller import THRESHOLD
    scored = await asyncio.to_thread(compute)
    return {
        "assets": [{**_slim_asset(a), "score": round(prob, 3)} for prob, a in scored],
        "threshold": THRESHOLD,
    }


@router.get("/pets/{name}/borderline/progress")
async def get_borderline_progress(name: str):
    return state.borderline_progress


@router.get("/suggestions/negatives")
async def get_neg_candidates(limit: int = 60):
    from poller import build_classifier, THRESHOLD
    config = data.load_config(DATA_DIR)

    all_pet_names = list(config.keys())
    ref_ids_per_pet = {n: data.load_pet_asset_ids(n, DATA_DIR) for n in all_pet_names}
    all_ref_ids: set[str] = {rid for ids in ref_ids_per_pet.values() for rid in ids}
    neg_ids = set(data.load_negative_ids(DATA_DIR))
    skipped_ids = set(data.load_skipped_ids(DATA_DIR))
    exclude = all_ref_ids | neg_ids | skipped_ids

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{imm.IMMICH_URL}/api/search/random",
            headers=imm.headers(),
            json={"count": 100, "type": "IMAGE"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    candidates = [a for a in resp.json() if isinstance(a, dict) and a.get("id") not in exclude]

    if not candidates:
        return {"assets": [], "threshold": THRESHOLD}

    pet_names = [n for n in all_pet_names if ref_ids_per_pet.get(n)]
    negative_ids = data.load_negative_ids(DATA_DIR)

    state.neg_request_id += 1
    my_id = state.neg_request_id

    def compute():
        state.neg_progress["current"] = 0
        state.neg_progress["total"] = 0
        state.neg_progress["running"] = True
        try:
            result = build_classifier(pet_names, ref_ids_per_pet, negative_ids)
            if result is None:
                return []
            names, clf, scaler = result
            unknown_idx = names.index("unknown") if "unknown" in names else -1
            state.neg_progress["total"] = len(candidates)
            scored = []
            for i, a in enumerate(candidates):
                if state.neg_request_id != my_id:
                    return []
                state.neg_progress["current"] = i + 1
                vec = embed_asset(a["id"])
                if vec is not None:
                    v = np.asarray(vec, dtype=np.float64).reshape(1, -1)
                    probs = clf.predict_proba(scaler.transform(v))[0]
                    pet_prob = (1.0 - float(probs[unknown_idx])) if unknown_idx >= 0 else 0.0
                    # Skip photos the classifier thinks are pets. Those belong in refs, not negatives.
                    if pet_prob < THRESHOLD:
                        scored.append((pet_prob, a))
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[:limit]
        finally:
            if state.neg_request_id == my_id:
                state.neg_progress["running"] = False

    scored = await asyncio.to_thread(compute)
    return {
        "assets": [{**_slim_asset(a), "score": round(prob, 3)} for prob, a in scored],
        "threshold": THRESHOLD,
    }


@router.get("/suggestions/negatives/progress")
async def get_neg_progress():
    return state.neg_progress


# ---------------------------------------------------------------------------
# Scan timestamp
# ---------------------------------------------------------------------------

@router.get("/poll-status")
async def get_poll_status():
    return data.load_poll_status(DATA_DIR)


@router.get("/timestamp")
async def get_timestamp():
    path = DATA_DIR / "last_scan_timestamp.txt"
    val = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    return {"timestamp": val}


class PetImport(BaseModel):
    person_id: str
    name: str
    description: str
    since: Optional[str] = None
    until: Optional[str] = None


@router.post("/pets/import")
async def import_pet(body: PetImport):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if any(c in name for c in r'/\.'):
        raise HTTPException(status_code=400, detail="Pet name cannot contain /, \\, or .")
    config = data.load_config(DATA_DIR)
    if name.lower() in {k.lower() for k in config}:
        raise HTTPException(status_code=409, detail=f"Pet '{name}' already exists")

    async with httpx.AsyncClient(timeout=15) as client:
        check = await client.get(f"{imm.IMMICH_URL}/api/people/{body.person_id}", headers=imm.headers())
    if check.status_code != 200:
        raise HTTPException(status_code=404, detail="Person not found in Immich")

    candidates = []
    async with httpx.AsyncClient(timeout=60) as client:
        search = await client.post(
            f"{imm.IMMICH_URL}/api/search/metadata",
            headers={**imm.headers(), "Content-Type": "application/json"},
            json={"personIds": [body.person_id], "size": 200},
        )
        if search.status_code == 200:
            block = search.json().get("assets", {})
            items = block.get("items", []) if isinstance(block, dict) else []
            for a in items:
                aid = a.get("id")
                if not aid:
                    continue
                faces_resp = await client.get(f"{imm.IMMICH_URL}/api/faces", headers=imm.headers(), params={"id": aid})
                if faces_resp.status_code == 200:
                    named = {f["person"]["id"] for f in faces_resp.json() if f and (f.get("person") or {}).get("id")}
                    if len(named) == 1:
                        candidates.append(aid)

    n = min(len(candidates), 20)
    assets = [{"asset_id": candidates[int(i * len(candidates) / n)], "face_id": None} for i in range(n)]

    (PETS_DIR / name).mkdir(parents=True, exist_ok=True)
    data.save_pet_refs(name, assets, DATA_DIR)
    config[name] = {"person_id": body.person_id, "description": body.description, "since": body.since, "until": body.until}
    data.save_config(config, DATA_DIR)
    log.info(f"Imported pet '{name}' from person_id={body.person_id} with {len(assets)} refs")
    return {"name": name, "person_id": body.person_id, "ref_count": len(assets)}


class TimestampBody(BaseModel):
    date: str


@router.post("/timestamp")
async def set_timestamp(body: TimestampBody):
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.date):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    ts = body.date + "T00:00:00.000Z"
    data.save_last_timestamp(ts, DATA_DIR)
    log.info(f"Scan timestamp reset to {ts}")
    return {"timestamp": ts}


@router.post("/scan")
async def trigger_scan():
    import state
    if state.scan_lock is not None and state.scan_lock.locked():
        state.scan_cancel.set()
    state.scan_generation += 1
    asyncio.create_task(_run_manual_scan(state.scan_generation))
    return {"status": "started"}


async def _run_manual_scan(generation: int):
    import state
    from poller import run_poll_cycle
    from datetime import datetime, timezone
    live_counts: dict = {}
    state.manual_scan_result = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "counts": live_counts}
    state.scan_low_conf_assets = []
    low_conf_assets: list = []

    def on_date(date_str):
        if isinstance(state.manual_scan_result, dict):
            state.manual_scan_result["current_date"] = date_str

    try:
        async with state.scan_lock:
            if state.scan_generation != generation:
                return
            state.scan_cancel.clear()
            await asyncio.to_thread(run_poll_cycle, DATA_DIR, on_date, state.scan_cancel, low_conf_assets, live_counts)
            if state.scan_generation == generation:
                state.scan_low_conf_assets = low_conf_assets
                state.manual_scan_result = data.load_poll_status(DATA_DIR)
    except Exception as e:
        if state.scan_generation == generation:
            state.manual_scan_result = {"status": "error", "error": str(e), "ran_at": datetime.now(timezone.utc).isoformat()}


@router.get("/scan/result")
async def get_scan_result():
    return state.manual_scan_result or {"status": "none"}


@router.get("/scan/low-confidence")
async def get_scan_low_confidence():
    from poller import THRESHOLD
    config = data.load_config(DATA_DIR)
    seen: dict = {}
    for a in (state.scan_low_conf_assets or []):
        aid = a["asset_id"]
        if aid not in seen or a["prob"] > seen[aid]["prob"]:
            seen[aid] = a
    sorted_assets = sorted(seen.values(), key=lambda a: a["prob"])
    return {
        "assets": [
            {"id": a["asset_id"], "thumb": f"/api/thumb/{a['asset_id']}",
             "pet_name": a["pet_name"], "score": a["prob"], "date": a.get("date", "")}
            for a in sorted_assets
        ],
        "pets": list(config.keys()),
        "threshold": THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Immich people list (for import)
# ---------------------------------------------------------------------------

@router.get("/immich-people")
async def list_immich_people():
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{imm.IMMICH_URL}/api/people", params={"withHidden": "false"}, headers=imm.headers())
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Failed to fetch people from Immich")
    body = resp.json()
    people = [{"id": p["id"], "name": p.get("name", "")} for p in body.get("people", []) if p.get("name")]
    return {"people": people}


# ---------------------------------------------------------------------------
# Thumbnail proxy
# ---------------------------------------------------------------------------

@router.get("/person-thumb/{person_id}")
async def person_thumbnail(person_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{imm.IMMICH_URL}/api/people/{person_id}/thumbnail", headers=imm.headers())
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code)
    return StreamingResponse(resp.aiter_bytes(), media_type=resp.headers.get("content-type", "image/jpeg"))


@router.get("/thumb/{asset_id}")
async def thumbnail(asset_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{imm.IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview", headers=imm.headers())
    return StreamingResponse(resp.aiter_bytes(), media_type=resp.headers.get("content-type", "image/jpeg"))
