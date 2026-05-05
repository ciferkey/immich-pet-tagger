"""
Poller: incremental classification using local CLIP model.
No DB access, embeddings computed from thumbnails via Immich HTTP API.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import requests
import torch
import open_clip
from PIL import Image
import io
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from immich_apis import fetch_assets_taken_after, fetch_asset_face_person_ids, IMMICH_BASE, IMMICH_API_KEY

log = logging.getLogger("poller")

THRESHOLD = float(os.environ.get("THRESHOLD", 0.92))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
FACE_BOX_SIZE = 256
STATE_FILE = "last_scan_timestamp.txt"

# CLIP model ViT-B/16 matches Immich's default
CLIP_MODEL_NAME = os.environ.get("CLIP_MODEL", "ViT-B-16")
CLIP_PRETRAINED = os.environ.get("CLIP_PRETRAINED", "openai")

_clip_model = None
_clip_preprocess = None
_clip_device = None


def get_clip():
    global _clip_model, _clip_preprocess, _clip_device
    if _clip_model is None:
        log.info(f"Loading CLIP model {CLIP_MODEL_NAME} ({CLIP_PRETRAINED})...")
        _clip_device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED
        )
        _clip_model.eval().to(_clip_device)
        log.info(f"CLIP loaded on {_clip_device}")
    return _clip_model, _clip_preprocess, _clip_device


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------

def load_config(data_dir: str) -> dict:
    path = Path(data_dir) / "config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_ref_ids(data_dir: str, pet_name: str) -> list[str]:
    """Return asset IDs only. Handles legacy (strings) and new (dicts) format."""
    path = Path(data_dir) / "pets" / pet_name / "refs.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data:
        return []
    if isinstance(data[0], str):
        return data
    return [r["asset_id"] for r in data]


def load_negative_ids(data_dir: str) -> list[str]:
    path = Path(data_dir) / "negatives.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def load_last_timestamp(data_dir: str) -> str:
    path = Path(data_dir) / STATE_FILE
    default = datetime.now(timezone.utc).date().isoformat() + "T00:00:00.000Z"
    if not path.exists():
        path.write_text(default + "\n", encoding="utf-8")
        return default
    val = path.read_text(encoding="utf-8").strip()
    return val if val else default


def save_last_timestamp(data_dir: str, ts: str) -> None:
    (Path(data_dir) / STATE_FILE).write_text(ts.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

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
# Thumbnail fetch + CLIP embedding
# ---------------------------------------------------------------------------

def fetch_thumbnail(asset_id: str) -> Image.Image | None:
    try:
        r = requests.get(
            f"{IMMICH_BASE}/api/assets/{asset_id}/thumbnail?size=preview",
            headers={"x-api-key": IMMICH_API_KEY},
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


def embed_asset(asset_id: str) -> np.ndarray | None:
    img = fetch_thumbnail(asset_id)
    if img is None:
        return None
    return embed_image(img)


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

    # Embed negatives as the unknown class
    if negative_ids:
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

    # If unknown class has no real samples, add a synthetic zero vector
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

def post_face(asset_id: str, person_id: str) -> str | None:
    """Returns face_id on success, None on failure.
    Immich returns 201 with empty body, so we fetch face_id via GET after creation."""
    try:
        r = requests.post(
            f"{IMMICH_BASE}/api/faces",
            json={
                "assetId": asset_id,
                "personId": person_id,
                "width": FACE_BOX_SIZE, "height": FACE_BOX_SIZE,
                "imageWidth": FACE_BOX_SIZE, "imageHeight": FACE_BOX_SIZE,
                "x": 0, "y": 0,
            },
            headers={"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json"},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            log.warning(f"post_face {asset_id} -> {r.status_code}: {r.text[:200]}")
            return None
        # Fetch face_id via GET since POST returns empty body
        fr = requests.get(
            f"{IMMICH_BASE}/api/faces",
            headers={"x-api-key": IMMICH_API_KEY},
            params={"id": asset_id},
            timeout=15,
        )
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

    config = load_config(data_dir)
    if not config:
        log.warning("config.json empty or missing, no pets configured yet.")
        return

    all_pet_names = list(config.keys())
    all_ref_ids = {name: load_ref_ids(data_dir, name) for name in all_pet_names}

    # Skip pets with no refs, they would pollute the classifier
    pet_names = [n for n in all_pet_names if len(all_ref_ids.get(n, [])) > 0]
    ref_ids_per_pet = {n: all_ref_ids[n] for n in pet_names}
    skipped = [n for n in all_pet_names if n not in pet_names]

    if skipped:
        log.warning(f"Skipping pets with no refs: {skipped}")
    if not pet_names:
        log.warning("No pets with reference assets, enroll pets via the UI first.")
        return

    log.info(f"Pets: {', '.join(f'{n}({len(ref_ids_per_pet[n])} refs)' for n in pet_names)}")

    negative_ids = load_negative_ids(data_dir)
    if negative_ids:
        log.info(f"Loaded {len(negative_ids)} negative samples")

    result = build_classifier(pet_names, ref_ids_per_pet, negative_ids)
    if result is None:
        return
    names, clf, scaler = result

    last_ts = load_last_timestamp(data_dir)
    log.info(f"Fetching assets taken after: {last_ts}")

    t0 = time.time()
    assets = fetch_assets_taken_after(last_ts)
    log.info(f"Fetched {len(assets)} assets in {time.time()-t0:.1f}s")

    if not assets:
        log.info("No new assets.")
        save_last_timestamp(data_dir, datetime.now(timezone.utc).isoformat())
        return

    counts = {"low_confidence": 0, "unknown": 0, "out_of_range": 0,
              "already_tagged": 0, "added": 0, "failed": 0, "no_thumb": 0}
    latest_ts = last_ts

    for aid, time_str in assets:
        if time_str > latest_ts:
            latest_ts = time_str

        vec = embed_asset(aid)
        if vec is None:
            counts["no_thumb"] += 1
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

        existing = fetch_asset_face_person_ids(aid)
        if person_id in existing:
            counts["already_tagged"] += 1
            continue

        log.info(f"{IMMICH_BASE}/search/photos/{aid} -> {pet_name} ({prob:.3f}) | {time_str[:10]}")

        if DRY_RUN:
            log.info(f"  dry-run: would add {pet_name}")
            counts["added"] += 1
        else:
            face_id = post_face(aid, person_id)
            if face_id:
                counts["added"] += 1
            else:
                counts["failed"] += 1

    log.info(f"Summary: {counts}")

    if not DRY_RUN:
        save_last_timestamp(data_dir, latest_ts)
        log.info(f"Saved timestamp: {latest_ts}")
