import json
import pytest
from pathlib import Path
import data


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_missing(tmp_path):
    assert data.load_config(tmp_path) == {}


def test_load_config_valid(tmp_path):
    cfg = {"cat": {"person_id": "abc"}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    assert data.load_config(tmp_path) == cfg


def test_load_config_corrupted(tmp_path):
    (tmp_path / "config.json").write_text("{bad json")
    assert data.load_config(tmp_path) == {}


def test_load_config_roundtrip(tmp_path):
    cfg = {"dog": {"person_id": "xyz", "since": "2024-01-01"}}
    data.save_config(cfg, tmp_path)
    assert data.load_config(tmp_path) == cfg


# ---------------------------------------------------------------------------
# load_pet_refs
# ---------------------------------------------------------------------------

def test_load_pet_refs_missing(tmp_path):
    assert data.load_pet_refs("cat", tmp_path) == []


def test_load_pet_refs_valid(tmp_path):
    refs = [{"asset_id": "a1", "face_id": "f1"}, {"asset_id": "a2", "face_id": None}]
    data.save_pet_refs("cat", refs, tmp_path)
    assert data.load_pet_refs("cat", tmp_path) == refs


def test_load_pet_refs_corrupted(tmp_path):
    ref_file = tmp_path / "pets" / "cat" / "refs.json"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("{broken")
    assert data.load_pet_refs("cat", tmp_path) == []


def test_load_pet_refs_legacy_strings(tmp_path):
    ref_file = tmp_path / "pets" / "cat" / "refs.json"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text(json.dumps(["id1", "id2"]))
    result = data.load_pet_refs("cat", tmp_path)
    assert result == [{"asset_id": "id1", "face_id": None}, {"asset_id": "id2", "face_id": None}]


def test_load_pet_refs_empty_file(tmp_path):
    ref_file = tmp_path / "pets" / "cat" / "refs.json"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("[]")
    assert data.load_pet_refs("cat", tmp_path) == []


# ---------------------------------------------------------------------------
# load_negative_ids
# ---------------------------------------------------------------------------

def test_load_negative_ids_missing(tmp_path):
    assert data.load_negative_ids(tmp_path) == []


def test_load_negative_ids_valid(tmp_path):
    ids = ["id1", "id2", "id3"]
    data.save_negative_ids(ids, tmp_path)
    assert data.load_negative_ids(tmp_path) == ids


def test_load_negative_ids_corrupted(tmp_path):
    (tmp_path / "negatives.json").write_text("not json at all")
    assert data.load_negative_ids(tmp_path) == []


# ---------------------------------------------------------------------------
# load_skipped_ids
# ---------------------------------------------------------------------------

def test_load_skipped_ids_missing(tmp_path):
    assert data.load_skipped_ids(tmp_path) == []


def test_load_skipped_ids_valid(tmp_path):
    ids = ["s1", "s2"]
    data.save_skipped_ids(ids, tmp_path)
    assert data.load_skipped_ids(tmp_path) == ids


def test_load_skipped_ids_corrupted(tmp_path):
    (tmp_path / "skipped.json").write_text("[unclosed")
    assert data.load_skipped_ids(tmp_path) == []


# ---------------------------------------------------------------------------
# load_poll_status
# ---------------------------------------------------------------------------

def test_load_poll_status_missing(tmp_path):
    assert data.load_poll_status(tmp_path) == {"status": "never"}


def test_load_poll_status_valid(tmp_path):
    payload = {"status": "idle", "ran_at": "2024-01-01T00:00:00Z"}
    (tmp_path / "last_poll_status.json").write_text(json.dumps(payload))
    assert data.load_poll_status(tmp_path) == payload


def test_load_poll_status_corrupted(tmp_path):
    (tmp_path / "last_poll_status.json").write_text("???")
    assert data.load_poll_status(tmp_path) == {"status": "never"}


# ---------------------------------------------------------------------------
# load_pet_asset_ids (dedup)
# ---------------------------------------------------------------------------

def test_load_pet_asset_ids_deduplicates(tmp_path):
    refs = [
        {"asset_id": "a1", "face_id": "f1"},
        {"asset_id": "a1", "face_id": "f2"},
        {"asset_id": "a2", "face_id": "f3"},
    ]
    data.save_pet_refs("cat", refs, tmp_path)
    result = data.load_pet_asset_ids("cat", tmp_path)
    assert result == ["a1", "a2"]
