"""Poller: incremental classification using a local CLIP model.
No DB access. Embeddings computed from thumbnails via the Immich HTTP API."""

import logging
import os
import pickle
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import io
import numpy as np
import open_clip
import requests
import torch
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import data
import immich as imm

log = logging.getLogger("poller")

THRESHOLD = float(os.environ.get("THRESHOLD", 0.92))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

CLIP_MODEL_NAME = os.environ.get("CLIP_MODEL", "ViT-B-16")
CLIP_PRETRAINED = os.environ.get("CLIP_PRETRAINED", "openai")

_clip_model = None
_clip_preprocess = None
_clip_device = None
_embed_cache: dict[str, np.ndarray] = {}
_cache_path: Path | None = None


def get_clip():
    global _clip_model, _clip_preprocess, _clip_device
    if _clip_model is None:
        log.info(f"Loading CLIP model {CLIP_MODEL_NAME} ({CLIP_PRETRAINED})...")
        _clip_device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED)
        _clip_model.eval().to(_clip_device)
        log.info(f"CLIP loaded on {_clip_device}")
    return _clip_model, _clip_preprocess, _clip_device


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

from datetime import date


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def asset_in_range(time_str: str, since: str | None, until: str | None) -> bool:
    d = parse_date(time_str)
    if d is None:
        return True
    if since and d < date.fromisoformat(since):
        return False
    if until and d > date.fromisoformat(until):
        return False
    return True


# ---------------------------------------------------------------------------
# Thumbnail fetch and CLIP embedding
# ---------------------------------------------------------------------------

def fetch_thumbnail(asset_id: str) -> Image.Image | None:
    try:
        r = requests.get(
            f"{imm.IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview",
            headers={"x-api-key": imm.IMMICH_API_KEY},
            timeout=15,
        )
        if r.status_code == 200 and r.content:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        log.warning(f"fetch_thumbnail {asset_id}: {e}")
    return None


def embed_image(img: Image.Image) -> np.ndarray | None:
    model, preprocess, device = get_clip()
    try:
        tensor = preprocess(img).unsqueeze(0).to(device)
        with torch.no_grad():
            feat = model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.cpu().numpy()[0]
    except Exception as e:
        log.warning(f"embed_image error: {e}")
        return None


def crop_animals(img: Image.Image) -> list[tuple[tuple, Image.Image]]:
    """Detect animals and return (bbox_norm, crop) pairs. Empty list means no animals found."""
    try:
        from detector import detect_animals
        boxes = detect_animals(img)
    except Exception as e:
        log.warning(f"YOLO detection failed: {e}")
        return []
    w, h = img.size
    return [
        (bbox, img.crop((int(bbox[0] * w), int(bbox[1] * h), int(bbox[2] * w), int(bbox[3] * h))))
        for bbox in boxes
    ]


def load_embed_cache(data_dir: Path) -> None:
    global _cache_path
    _cache_path = data_dir / "embeddings.pkl"
    if _cache_path.exists():
        try:
            with open(_cache_path, "rb") as f:
                _embed_cache.update(pickle.load(f))
            log.info(f"Loaded {len(_embed_cache)} cached embeddings from {_cache_path}")
        except Exception as e:
            log.warning(f"Could not load embedding cache: {e}")


def _save_embed_cache() -> None:
    if _cache_path is None:
        return
    tmp = _cache_path.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as f:
            pickle.dump(_embed_cache, f)
        tmp.replace(_cache_path)
    except Exception as e:
        log.warning(f"Could not save embedding cache: {e}")


def embed_asset(asset_id: str) -> np.ndarray | None:
    if asset_id in _embed_cache:
        return _embed_cache[asset_id]
    img = fetch_thumbnail(asset_id)
    if img is None:
        return None
    crops = crop_animals(img)
    vec = embed_image(crops[0][1]) if crops else None
    if vec is None:
        vec = embed_image(img)
    if vec is not None:
        _embed_cache[asset_id] = vec
        _save_embed_cache()
    return vec


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def build_classifier(
    pet_names: list[str],
    ref_ids_per_pet: dict[str, list[str]],
    negative_ids: list[str] | None = None,
) -> tuple[list[str], LogisticRegression, StandardScaler] | None:
    all_vecs = []
    all_labels = []
    unknown_idx = len(pet_names)
    names = pet_names + ["unknown"]

    for i, name in enumerate(pet_names):
        ids = ref_ids_per_pet.get(name, [])
        log.info(f"Embedding {len(ids)} refs for '{name}'...")
        for aid in ids:
            vec = embed_asset(aid)
            if vec is not None:
                all_vecs.append(vec)
                all_labels.append(i)
            else:
                log.warning(f"  Could not embed ref {aid} for '{name}'")

    # Balance negatives to ~3x total pet refs
    total_refs = sum(len(ids) for ids in ref_ids_per_pet.values())
    if negative_ids:
        target = total_refs * 3
        if len(negative_ids) > target:
            negative_ids = random.sample(negative_ids, target)
            log.info(f"Subsampled negatives to {target} (3x {total_refs} refs)")
        elif len(negative_ids) < total_refs * 2:
            log.warning(f"{len(negative_ids)} negatives for {total_refs} refs — aim for {total_refs * 2}-{target} for best accuracy")

        log.info(f"Embedding {len(negative_ids)} negative samples...")
        for aid in negative_ids:
            vec = embed_asset(aid)
            if vec is not None:
                all_vecs.append(vec)
                all_labels.append(unknown_idx)

    if not all_vecs:
        log.warning("No embeddings computed, skipping classifier training.")
        return None

    X = np.array(all_vecs, dtype=np.float64)
    y = np.array(all_labels, dtype=np.intp)

    if unknown_idx not in y:
        X = np.vstack([X, np.zeros((1, X.shape[1]))])
        y = np.append(y, unknown_idx)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    clf = LogisticRegression(max_iter=1000, random_state=0)
    clf.fit(X_scaled, y)
    log.info(f"Classifier trained on {len(y)} samples, classes: {names} ({sum(y==unknown_idx)} unknown)")
    return names, clf, scaler


def classify(vec, names, clf, scaler) -> tuple[str, float]:
    v = np.asarray(vec, dtype=np.float64).reshape(1, -1)
    probs = clf.predict_proba(scaler.transform(v))[0]
    i = int(np.argmax(probs))
    return names[i], float(probs[i])


# ---------------------------------------------------------------------------
# Face assignment
# ---------------------------------------------------------------------------

def post_face(asset_id: str, person_id: str, bbox_norm=None, img_size=None) -> str | None:
    """Returns face_id on success, None on failure."""
    if bbox_norm is not None and img_size is not None:
        x1, y1, x2, y2 = bbox_norm
        iw, ih = img_size
        bx, by = int(x1 * iw), int(y1 * ih)
        bw, bh = int((x2 - x1) * iw), int((y2 - y1) * ih)
    else:
        bx, by, bw, bh = 0, 0, imm.FACE_BOX_SIZE, imm.FACE_BOX_SIZE
        iw, ih = imm.FACE_BOX_SIZE, imm.FACE_BOX_SIZE
    try:
        r = requests.post(
            f"{imm.IMMICH_URL}/api/faces",
            json={"assetId": asset_id, "personId": person_id,
                  "width": bw, "height": bh,
                  "imageWidth": iw, "imageHeight": ih,
                  "x": bx, "y": by},
            headers={**imm.headers(), "Content-Type": "application/json"},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            log.warning(f"post_face {asset_id} -> {r.status_code}: {r.text[:200]}")
            return None
        fr = requests.get(f"{imm.IMMICH_URL}/api/faces", headers=imm.headers(), params={"id": asset_id}, timeout=15)
        if fr.status_code == 200:
            for face in fr.json():
                if face.get("person", {}).get("id") == person_id:
                    return face.get("id")
        log.warning(f"post_face: created but could not retrieve face_id for asset {asset_id}")
        return None
    except Exception as e:
        log.error(f"post_face error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main poll cycle
# ---------------------------------------------------------------------------

def run_poll_cycle(data_dir: str) -> None:
    log.info(f"Poll cycle | threshold={THRESHOLD} dry_run={DRY_RUN}")
    dd = Path(data_dir)
    now = datetime.now(timezone.utc).isoformat()
    data.write_poll_status(dd, {"status": "running", "started_at": now})

    counts = {"added": 0, "low_confidence": 0, "unknown": 0,
              "out_of_range": 0, "already_tagged": 0, "failed": 0, "no_thumb": 0}
    try:
        _run_poll_cycle(dd, counts)
    except Exception as e:
        data.write_poll_status(dd, {"status": "error", "ran_at": datetime.now(timezone.utc).isoformat(), "error": str(e), "counts": counts})
        raise
    else:
        data.write_poll_status(dd, {"status": "idle", "ran_at": datetime.now(timezone.utc).isoformat(), "counts": counts})


def _run_poll_cycle(dd: Path, counts: dict) -> None:
    config = data.load_config(dd)
    if not config:
        log.warning("config.json empty or missing, no pets configured yet.")
        return

    all_pet_names = list(config.keys())
    all_ref_ids = {name: data.load_pet_asset_ids(name, dd) for name in all_pet_names}

    pet_names = [n for n in all_pet_names if all_ref_ids.get(n)]
    ref_ids_per_pet = {n: all_ref_ids[n] for n in pet_names}
    skipped = [n for n in all_pet_names if n not in pet_names]

    if skipped:
        log.warning(f"Skipping pets with no refs: {skipped}")
    if not pet_names:
        log.warning("No pets with reference assets, enroll pets via the UI first.")
        return

    log.info(f"Pets: {', '.join(f'{n}({len(ref_ids_per_pet[n])} refs)' for n in pet_names)}")

    negative_ids = data.load_negative_ids(dd)
    if negative_ids:
        log.info(f"Loaded {len(negative_ids)} negative samples")

    result = build_classifier(pet_names, ref_ids_per_pet, negative_ids)
    if result is None:
        return
    names, clf, scaler = result

    last_ts = data.load_last_timestamp(dd)
    log.info(f"Fetching assets taken after: {last_ts}")

    t0 = time.time()
    assets = imm.fetch_assets_taken_after(last_ts)
    log.info(f"Fetched {len(assets)} assets in {time.time()-t0:.1f}s")

    if not assets:
        log.info("No new assets.")
        data.save_last_timestamp(datetime.now(timezone.utc).isoformat(), dd)
        return

    latest_ts = last_ts

    for aid, time_str in assets:
        if time_str > latest_ts:
            latest_ts = time_str

        img = fetch_thumbnail(aid)
        if img is None:
            counts["no_thumb"] += 1
            continue

        crops = crop_animals(img)
        if not crops:
            crops = [(None, img)]

        existing_persons = imm.fetch_asset_face_person_ids(aid)
        tagged_in_photo: set[str] = set()

        for bbox_norm, crop in crops:
            vec = embed_image(crop)
            if vec is None:
                continue

            pet_name, prob = classify(vec, names, clf, scaler)

            if pet_name == "unknown":
                counts["unknown"] += 1
                continue

            if prob < THRESHOLD:
                counts["low_confidence"] += 1
                continue

            cfg = config.get(pet_name, {})
            if not asset_in_range(time_str, cfg.get("since"), cfg.get("until")):
                counts["out_of_range"] += 1
                continue

            person_id = cfg.get("person_id")
            if not person_id:
                log.warning(f"Pet '{pet_name}' has no person_id in config.")
                continue

            if person_id in existing_persons or person_id in tagged_in_photo:
                counts["already_tagged"] += 1
                continue

            log.info(f"{imm.IMMICH_URL}/search/photos/{aid} -> {pet_name} ({prob:.3f}) | {time_str[:10]}")

            if DRY_RUN:
                log.info(f"  dry-run: would add {pet_name}")
                counts["added"] += 1
                tagged_in_photo.add(person_id)
            else:
                face_id = post_face(aid, person_id, bbox_norm, img.size if bbox_norm is not None else None)
                if face_id:
                    counts["added"] += 1
                    tagged_in_photo.add(person_id)
                else:
                    counts["failed"] += 1

    log.info(f"Summary: {counts}")

    if not DRY_RUN:
        data.save_last_timestamp(latest_ts, dd)
        log.info(f"Saved timestamp: {latest_ts}")
