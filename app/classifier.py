"""Logistic regression classifier over CLIP embeddings."""

import logging
import random

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import embedder as emb

log = logging.getLogger("classifier")


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
            vec = emb.embed_asset(aid, require_animal=True)
            if vec is not None:
                all_vecs.append(vec)
                all_labels.append(i)
            else:
                log.warning(f"  Skipped ref {aid} for '{name}' (no animal detected by YOLO)")

    total_refs = sum(len(ids) for ids in ref_ids_per_pet.values())
    if negative_ids:
        target = total_refs * 3
        if len(negative_ids) > target:
            negative_ids = random.sample(negative_ids, target)
            log.info(f"Subsampled negatives to {target} (3x {total_refs} refs)")

        log.info(f"Embedding {len(negative_ids)} negative samples...")
        for aid in negative_ids:
            vec = emb.embed_asset(aid)
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
