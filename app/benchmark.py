"""Benchmark classifier accuracy against manually tagged photos in Immich.

Usage (inside the container):
    python benchmark.py

Run from the host:
    docker compose exec pet-tagger python benchmark.py
"""

import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

import classifier as clf_mod
import data
import embedder as emb
import immich as imm

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
NEG_TEST_SIZE = int(os.environ.get("BENCH_NEG_SIZE", 200))


def fetch_tagged_assets(person_id: str) -> list[str]:
    url = f"{imm.IMMICH_URL}/api/search/metadata"
    hdrs = {**imm.headers(), "Content-Type": "application/json"}
    out: list[str] = []
    page = 1
    while True:
        r = requests.post(url, json={"personIds": [person_id], "page": page, "size": 1000}, headers=hdrs, timeout=30)
        if r.status_code != 200:
            print(f"  fetch_tagged_assets: status={r.status_code}", file=sys.stderr)
            break
        body = r.json()
        block = body.get("assets") or {}
        items = (block.get("items") if isinstance(block, dict) else None) or body.get("items") or []
        out.extend(a["id"] for a in items if a.get("id") and a.get("type") == "IMAGE")
        if len(items) < 1000:
            break
        page += 1
    return out


def fetch_recent_asset_sample(n: int) -> list[str]:
    """Fetch a random sample of recent asset IDs from Immich (no person filter)."""
    url = f"{imm.IMMICH_URL}/api/search/metadata"
    hdrs = {**imm.headers(), "Content-Type": "application/json"}
    out: list[str] = []
    page = 1
    while len(out) < n * 5:
        r = requests.post(url, json={"page": page, "size": 1000}, headers=hdrs, timeout=30)
        if r.status_code != 200:
            break
        body = r.json()
        block = body.get("assets") or {}
        items = (block.get("items") if isinstance(block, dict) else None) or body.get("items") or []
        out.extend(a["id"] for a in items if a.get("id") and a.get("type") == "IMAGE")
        if len(items) < 1000:
            break
        page += 1
    random.shuffle(out)
    return out[:n * 5]


def main() -> None:
    config = data.load_config(DATA_DIR)
    if not config:
        print("No pets configured.")
        return

    pet_names = list(config.keys())
    refs_per_pet = {
        name: data.load_pet_refs(config[name].get("person_id") or name, DATA_DIR)
        for name in pet_names
    }
    pet_names = [n for n in pet_names if refs_per_pet.get(n)]
    if not pet_names:
        print("No pets with refs.")
        return

    negative_ids = data.load_negative_ids(DATA_DIR)

    print("Building classifier...")
    result = clf_mod.build_classifier(pet_names, refs_per_pet, negative_ids)
    if result is None:
        print("Could not build classifier.")
        return
    names, clf, scaler = result

    ref_id_set = {r["asset_id"] for refs in refs_per_pet.values() for r in refs}
    pet_person_ids = {config[n]["person_id"] for n in pet_names if config[n].get("person_id")}

    # --- Positive test set: tagged photos for each pet ---
    test_assets: list[tuple[str, str]] = []
    for pet_name in pet_names:
        person_id = config[pet_name].get("person_id")
        if not person_id:
            continue
        print(f"Fetching tagged assets for {pet_name}...")
        tagged = fetch_tagged_assets(person_id)
        non_ref = [aid for aid in tagged if aid not in ref_id_set]
        excluded = len(tagged) - len(non_ref)
        print(f"  {len(tagged)} tagged, {excluded} refs excluded, {len(non_ref)} for testing")
        test_assets.extend((aid, pet_name) for aid in non_ref)

    if not test_assets:
        print("No test assets found. Tag some photos first.")
        return

    # --- Negative test set: recent assets that don't contain any pet ---
    print(f"\nFetching candidate assets for negative test set...")
    candidates = fetch_recent_asset_sample(NEG_TEST_SIZE)
    test_asset_ids = {aid for aid, _ in test_assets}
    neg_test_ids_set = set(negative_ids) if negative_ids else set()

    print(f"  Checking {len(candidates)} candidates for pet faces...")
    neg_test: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(imm.fetch_asset_face_person_ids, aid): aid for aid in candidates}
        for future in as_completed(futures):
            aid = futures[future]
            try:
                person_ids = future.result()
            except Exception:
                continue
            if aid in ref_id_set or aid in neg_test_ids_set or aid in test_asset_ids:
                continue
            if pet_person_ids.isdisjoint(person_ids):
                neg_test.append(aid)
            if len(neg_test) >= NEG_TEST_SIZE:
                break

    print(f"  {len(neg_test)} non-pet assets selected for negative testing")

    # --- Classify all assets ---
    all_work: list[tuple[str, str]] = test_assets + [(aid, "unknown") for aid in neg_test]
    print(f"\nClassifying {len(all_work)} assets ({len(test_assets)} pets, {len(neg_test)} non-pet) with {emb.SCAN_WORKERS} workers...")

    # counts[path][true_name][pred] where path is "yolo" or "fallback"
    counts: dict[str, dict[str, dict[str, int]]] = {
        "yolo": defaultdict(lambda: defaultdict(int)),
        "fallback": defaultdict(lambda: defaultdict(int)),
    }
    # failures[path][true_name] = list of (asset_id, predicted)
    failures: dict[str, dict[str, list[tuple[str, str]]]] = {
        "yolo": defaultdict(list),
        "fallback": defaultdict(list),
    }
    skipped = 0

    def classify_one(aid: str, true_name: str) -> tuple[str, str | None, str]:
        img = emb.fetch_thumbnail(aid)
        if img is None:
            return true_name, None, "yolo"
        crops = emb.crop_animals(img)
        if crops:
            path = "yolo"
            vecs = [v for v in (emb.embed_image(crop) for _, crop in crops) if v is not None]
            if not vecs:
                return true_name, None, path
            preds = [clf_mod.classify(v, names, clf, scaler)[0] for v in vecs]
            # For pet photos: correct if any crop matched. For non-pet: report worst case (any false positive).
            if true_name != "unknown":
                pred = true_name if true_name in preds else preds[0]
            else:
                pred = next((p for p in preds if p != "unknown"), "unknown")
        else:
            path = "fallback"
            vec = emb.embed_image(img)
            if vec is None:
                return true_name, None, path
            pred, _ = clf_mod.classify(vec, names, clf, scaler)
        return true_name, pred, path

    with ThreadPoolExecutor(max_workers=emb.SCAN_WORKERS) as executor:
        futures = {executor.submit(classify_one, aid, name): (aid, name) for aid, name in all_work}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(all_work)}")
            f_aid, f_name = futures[future]
            try:
                true_name, pred, path = future.result()
                if pred is None:
                    skipped += 1
                else:
                    counts[path][true_name][pred] += 1
                    if true_name != "unknown" and pred != true_name:
                        failures[path][true_name].append((f_aid, pred))
                    elif true_name == "unknown" and pred != "unknown":
                        failures[path]["__fp__"].append((f_aid, pred))
            except Exception as e:
                print(f"  error: {e}", file=sys.stderr)
                skipped += 1

    for path_label, path_counts in (("YOLO crop", counts["yolo"]), ("Whole-image fallback", counts["fallback"])):
        total_in_path = sum(sum(p.values()) for p in path_counts.values())
        if not total_in_path:
            continue

        print(f"\n--- Recall [{path_label}] ---")
        total_correct = 0
        total_count = 0
        for true_name in pet_names:
            preds = path_counts[true_name]
            count = sum(preds.values())
            if not count:
                continue
            correct = preds.get(true_name, 0)
            acc = correct / count * 100
            wrong = {k: v for k, v in preds.items() if k != true_name}
            wrong_str = f"  misclassified: {wrong}" if wrong else ""
            print(f"{true_name:20s} {correct:4d}/{count:<4d}  {acc:5.1f}%{wrong_str}")
            total_correct += correct
            total_count += count

        if total_count:
            overall_recall = total_correct / total_count * 100
            print(f"\n{'Overall recall':20s} {total_correct:4d}/{total_count:<4d}  {overall_recall:.1f}%")

        neg_preds = path_counts["unknown"]
        neg_count = sum(neg_preds.values())
        if neg_count:
            neg_correct = neg_preds.get("unknown", 0)
            false_positives = {k: v for k, v in neg_preds.items() if k != "unknown"}
            fp_count = sum(false_positives.values())
            fp_rate = fp_count / neg_count * 100
            print(f"\n--- Precision [{path_label}] ---")
            print(f"{'Non-pet assets':20s} {neg_correct:4d}/{neg_count:<4d}  correctly unknown  FP rate: {fp_rate:.1f}%")
            if false_positives:
                print(f"  false positives: {false_positives}")

    print("\n--- Failed asset IDs ---")
    for path_label, path_key in (("YOLO crop", "yolo"), ("Whole-image fallback", "fallback")):
        for true_name, fails in failures[path_key].items():
            if fails:
                label = "false positives (non-pet)" if true_name == "__fp__" else f"{true_name} misclassified"
                print(f"\n[{path_label}] {label}:")
                for aid, pred in fails:
                    print(f"  {aid}  -> {pred}")

    if skipped:
        print(f"\n({skipped} assets skipped: no thumbnail or embedding failed)")


if __name__ == "__main__":
    main()
