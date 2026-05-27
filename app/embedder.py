"""CLIP batch inference workers and image embedding."""

import io
import logging
import os
import pickle
import queue
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import open_clip
import requests
import torch
from PIL import Image

import immich as imm

log = logging.getLogger("embedder")

GPU_WORKERS = int(os.environ.get("GPU_WORKERS", 2))
_default_scan_workers = GPU_WORKERS * 32 if torch.cuda.is_available() else 8
SCAN_WORKERS = int(os.environ.get("SCAN_WORKERS", _default_scan_workers))
CLIP_BATCH_SIZE = int(os.environ.get("CLIP_BATCH_SIZE", 32))
CLIP_MODEL_NAME = "ViT-B-16"
CLIP_PRETRAINED = "openai"

MAX_EMBED_CACHE_SIZE = int(os.environ.get("EMBED_CACHE_SIZE", 5000))
_embed_cache: OrderedDict[str, list[np.ndarray]] = OrderedDict()
_cache_path: Path | None = None
_cache_dirty = False
_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# CLIP batch workers
# ---------------------------------------------------------------------------

# Preprocess transform shared across worker threads (set by first CLIP worker).
# Worker threads do CPU preprocessing; batch threads only stack + run GPU.
_clip_preprocess_fn = None
_clip_preprocess_ready = threading.Event()


class _EmbedReq:
    __slots__ = ("tensor", "event", "result")

    def __init__(self, tensor: torch.Tensor):
        self.tensor = tensor
        self.event = threading.Event()
        self.result: np.ndarray | None = None


_embed_queue: queue.Queue[_EmbedReq] = queue.Queue()
_clip_worker_threads: list[threading.Thread] = []
_clip_worker_lock = threading.Lock()

_clip_batch_total = 0
_clip_batch_count = 0
_stats_lock = threading.Lock()


def reset_batch_stats() -> None:
    global _clip_batch_total, _clip_batch_count
    with _stats_lock:
        _clip_batch_total = _clip_batch_count = 0


def get_avg_batch_size() -> float:
    with _stats_lock:
        return _clip_batch_total / _clip_batch_count if _clip_batch_count else 0.0


def _clip_batch_loop(worker_id: int) -> None:
    global _clip_batch_total, _clip_batch_count, _clip_preprocess_fn
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"CLIP worker {worker_id} loading on {device}...")
    model, preprocess, _ = open_clip.create_model_and_transforms(CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED)
    model.eval().to(device)
    if not _clip_preprocess_ready.is_set():
        _clip_preprocess_fn = preprocess
        _clip_preprocess_ready.set()
    stream = torch.cuda.Stream() if device == "cuda" else None
    log.info(f"CLIP worker {worker_id} ready")

    while True:
        first = _embed_queue.get()
        batch = [first]
        try:
            while len(batch) < CLIP_BATCH_SIZE:
                batch.append(_embed_queue.get_nowait())
        except queue.Empty:
            pass

        with _stats_lock:
            _clip_batch_total += len(batch)
            _clip_batch_count += 1

        try:
            stacked = torch.stack([req.tensor for req in batch])
            if stream is not None:
                with torch.cuda.stream(stream):
                    tensors = stacked.to(device, non_blocking=True)
                    with torch.no_grad():
                        feats = model.encode_image(tensors)
                        feats = feats / feats.norm(dim=-1, keepdim=True)
                stream.synchronize()
                vecs = feats.cpu().numpy()
            else:
                with torch.no_grad():
                    feats = model.encode_image(stacked.to(device))
                    feats = feats / feats.norm(dim=-1, keepdim=True)
                vecs = feats.cpu().numpy()
        except Exception as e:
            log.warning(f"CLIP worker {worker_id} batch error: {e}")
            vecs = [None] * len(batch)

        for req, vec in zip(batch, vecs):
            req.result = vec
            req.event.set()


def _ensure_clip_workers() -> None:
    with _clip_worker_lock:
        alive = [t for t in _clip_worker_threads if t.is_alive()]
        for i in range(len(alive), GPU_WORKERS):
            t = threading.Thread(target=_clip_batch_loop, args=(i,), daemon=True, name=f"clip-batch-{i}")
            t.start()
            _clip_worker_threads.append(t)


# ---------------------------------------------------------------------------
# Thumbnail fetch and CLIP embedding
# ---------------------------------------------------------------------------

def fetch_thumbnail(asset_id: str) -> Image.Image | None:
    try:
        r = requests.get(
            f"{imm.IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview",
            headers={"x-api-key": imm.IMMICH_API_KEY},
            timeout=30,
        )
        if r.status_code == 200 and r.content:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        log.warning(f"fetch_thumbnail {asset_id}: {e}")
    return None


def embed_image(img: Image.Image) -> np.ndarray | None:
    _ensure_clip_workers()
    _clip_preprocess_ready.wait()  # blocks only until first CLIP worker is up
    tensor = _clip_preprocess_fn(img)  # CPU preprocessing in caller's thread
    req = _EmbedReq(tensor)
    _embed_queue.put(req)
    req.event.wait()
    return req.result


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


def get_crops_and_embed(asset_id: str) -> list[tuple[dict, np.ndarray]]:
    """Fetch thumbnail once, run YOLO, embed each crop. Returns [(crop_info, vec), ...]."""
    img = fetch_thumbnail(asset_id)
    if img is None:
        return []
    crops = crop_animals(img)
    if not crops:
        return []
    result = []
    for i, (bbox, crop_img) in enumerate(crops):
        vec = embed_image(crop_img)
        if vec is not None:
            result.append(({"crop_idx": i, "bbox": list(bbox)}, vec))
    return result


def embed_crop_by_bbox(asset_id: str, bbox: list) -> np.ndarray | None:
    """Embed a specific crop by normalized bounding box. Used for crop-centric refs."""
    with _cache_lock:
        cached = _embed_cache.get(asset_id)
        if cached is not None:
            _embed_cache.move_to_end(asset_id)
    if cached is not None:
        vecs = cached if isinstance(cached, list) else [cached]
        if len(vecs) == 1:
            return vecs[0]
    img = fetch_thumbnail(asset_id)
    if img is None:
        return None
    w, h = img.size
    x1, y1, x2, y2 = bbox
    crop_img = img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))
    return embed_image(crop_img)


def load_embed_cache(data_dir: Path) -> None:
    global _cache_path
    _cache_path = data_dir / "embeddings.pkl"
    if _cache_path.exists():
        try:
            with open(_cache_path, "rb") as f:
                loaded = pickle.load(f)
            with _cache_lock:
                _embed_cache.update(loaded)
                while len(_embed_cache) > MAX_EMBED_CACHE_SIZE:
                    _embed_cache.popitem(last=False)
            log.info(f"Loaded {len(_embed_cache)} cached embeddings from {_cache_path}")
        except Exception as e:
            log.warning(f"Could not load embedding cache: {e}")


def _save_embed_cache() -> None:
    global _cache_dirty
    if _cache_path is None:
        return
    with _cache_lock:
        if not _cache_dirty:
            return
        snapshot = dict(_embed_cache)
        _cache_dirty = False
    tmp = _cache_path.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as f:
            pickle.dump(snapshot, f)
        tmp.replace(_cache_path)
    except Exception as e:
        log.warning(f"Could not save embedding cache: {e}")


def save_embed_cache() -> None:
    """Flush the embedding cache to disk. Call at the end of each scan cycle."""
    _save_embed_cache()


def embed_asset_crops(asset_id: str, require_animal: bool = False) -> list[np.ndarray]:
    """Return one embedding per detected animal crop. Falls back to full image if no crops and require_animal is False."""
    global _cache_dirty
    with _cache_lock:
        cached = _embed_cache.get(asset_id)
        if cached is not None:
            _embed_cache.move_to_end(asset_id)
    if cached is not None:
        return cached if isinstance(cached, list) else [cached]
    img = fetch_thumbnail(asset_id)
    if img is None:
        return []
    crops = crop_animals(img)
    if not crops:
        if require_animal:
            return []
        vec = embed_image(img)
        vecs = [vec] if vec is not None else []
    else:
        vecs = [v for v in (embed_image(crop_img) for _, crop_img in crops) if v is not None]
    if vecs:
        with _cache_lock:
            _embed_cache[asset_id] = vecs
            _embed_cache.move_to_end(asset_id)
            if len(_embed_cache) > MAX_EMBED_CACHE_SIZE:
                _embed_cache.popitem(last=False)
            _cache_dirty = True
    return vecs


def embed_asset(asset_id: str, require_animal: bool = False) -> np.ndarray | None:
    vecs = embed_asset_crops(asset_id, require_animal)
    return vecs[0] if vecs else None


def resolve_bbox(asset_id: str) -> list | None:
    """Return the first YOLO bounding box for an asset, or None if no animal detected."""
    img = fetch_thumbnail(asset_id)
    if img is None:
        return None
    crops = crop_animals(img)
    return list(crops[0][0]) if crops else None
