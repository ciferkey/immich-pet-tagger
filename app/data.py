"""File I/O helpers. All functions take an explicit data_dir Path."""

import json
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(data_dir: Path) -> dict:
    f = data_dir / "config.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_config(config: dict, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pet refs
# ---------------------------------------------------------------------------

def load_pet_refs(pet_name: str, data_dir: Path) -> list[dict]:
    """Return list of {asset_id, face_id}. Handles legacy list-of-strings format."""
    ref_file = data_dir / "pets" / pet_name / "refs.json"
    if not ref_file.exists():
        return []
    data = json.loads(ref_file.read_text(encoding="utf-8"))
    if not data:
        return []
    if isinstance(data[0], str):
        return [{"asset_id": aid, "face_id": None} for aid in data]
    return data


def load_pet_asset_ids(pet_name: str, data_dir: Path) -> list[str]:
    return [r["asset_id"] for r in load_pet_refs(pet_name, data_dir)]


def save_pet_refs(pet_name: str, refs: list[dict], data_dir: Path) -> None:
    pet_dir = data_dir / "pets" / pet_name
    pet_dir.mkdir(parents=True, exist_ok=True)
    (pet_dir / "refs.json").write_text(json.dumps(refs, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Negatives
# ---------------------------------------------------------------------------

def load_negative_ids(data_dir: Path) -> list[str]:
    path = data_dir / "negatives.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_negative_ids(ids: list[str], data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "negatives.json").write_text(json.dumps(ids, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scan timestamp
# ---------------------------------------------------------------------------

def load_last_timestamp(data_dir: Path) -> str:
    path = data_dir / "last_scan_timestamp.txt"
    default = datetime.now(timezone.utc).date().isoformat() + "T00:00:00.000Z"
    if not path.exists():
        path.write_text(default + "\n", encoding="utf-8")
        return default
    val = path.read_text(encoding="utf-8").strip()
    return val if val else default


def save_last_timestamp(ts: str, data_dir: Path) -> None:
    (data_dir / "last_scan_timestamp.txt").write_text(ts.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Poll status
# ---------------------------------------------------------------------------

def load_poll_status(data_dir: Path) -> dict:
    path = data_dir / "last_poll_status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "never"}


def write_poll_status(data_dir: Path, payload: dict) -> None:
    try:
        (data_dir / "last_poll_status.json").write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
