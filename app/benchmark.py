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
        out.extend(a["id"] for a in items if a.get("id"))
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
        out.extend(a["id"] for a in items if a.get("id"))
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

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    skipped = 0

    def classify_one(aid: str, true_name: str) -> tuple[str, str | None]:
        vec = emb.embed_asset(aid)
        if vec is None:
            return true_name, None
        pred, _ = clf_mod.classify(vec, names, clf, scaler)
        return true_name, pred

    with ThreadPoolExecutor(max_workers=emb.SCAN_WORKERS) as executor:
        futures = {executor.submit(classify_one, aid, name): (aid, name) for aid, name in all_work}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(all_work)}")
            try:
                true_name, pred = future.result()
                if pred is None:
                    skipped += 1
                else:
                    counts[true_name][pred] += 1
            except Exception as e:
                print(f"  error: {e}", file=sys.stderr)
                skipped += 1

    print("\n--- Recall (pet photos correctly identified) ---")
    total_correct = 0
    total_count = 0
    for true_name in pet_names:
        preds = counts[true_name]
        count = sum(preds.values())
        correct = preds.get(true_name, 0)
        acc = correct / count * 100 if count else 0.0
        wrong = {k: v for k, v in preds.items() if k != true_name}
        wrong_str = f"  misclassified: {wrong}" if wrong else ""
        print(f"{true_name:20s} {correct:4d}/{count:<4d}  {acc:5.1f}%{wrong_str}")
        total_correct += correct
        total_count += count

    overall_recall = total_correct / total_count * 100 if total_count else 0.0
    print(f"\n{'Overall recall':20s} {total_correct:4d}/{total_count:<4d}  {overall_recall:.1f}%")

    if neg_test:
        neg_preds = counts["unknown"]
        neg_count = sum(neg_preds.values())
        neg_correct = neg_preds.get("unknown", 0)
        false_positives = {k: v for k, v in neg_preds.items() if k != "unknown"}
        fp_count = sum(false_positives.values())
        fp_rate = fp_count / neg_count * 100 if neg_count else 0.0
        print(f"\n--- Precision (non-pet photos NOT tagged as a pet) ---")
        print(f"{'Non-pet assets':20s} {neg_correct:4d}/{neg_count:<4d}  correctly unknown  FP rate: {fp_rate:.1f}%")
        if false_positives:
            print(f"  false positives: {false_positives}")

    if skipped:
        print(f"\n({skipped} assets skipped: no thumbnail or embedding failed)")


if __name__ == "__main__":
    main()
