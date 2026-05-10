# immich-pet-tagger

Automatic pet tagging for Immich. Identifies your pets in new photos and tags them as people in Immich, the same way Immich tags human faces, but for cats, dogs, or any visually distinct subject.

Uses CLIP embeddings and a few reference photos you provide. No cloud services, no training required, runs entirely on your own hardware as a Docker sidecar alongside Immich.

## How it works

1. You enroll your pets via a web UI: provide a few reference photos and a short description
2. A logistic regression classifier is trained locally on CLIP embeddings of those references
3. Every 5 minutes, new photos are classified and matching pets are tagged in Immich
4. Pets appear in Immich's People section just like humans

## Features

- **Import from Immich**: if Immich already recognizes your pet as a person, import them in one click. The tool picks up to 20 evenly distributed reference photos automatically.
- **Find similar photos**: two-stage search — Immich's CLIP text search narrows the field, then the local classifier ranks by pet probability. Instant when the pet has no refs yet.
- **Find candidates for "not my pets"**: searches across all pets at once, scores results by pet-likeness, and surfaces the top 60 most confusable photos for bulk review.
- **Negative samples**: mark photos that look like your pet but aren't, to sharpen the classifier's ability to reject false positives.
- **Tagged photos panel**: review all photos currently tagged for a pet in Immich; remove tags or mark as "not my pets" in bulk.
- **Date ranges**: restrict a pet to photos taken within a specific period (useful for pets that have passed away or were adopted later).
- **Scan controls**: set the scan start date and trigger a scan from the sidebar; the last scan stats are shown live.
- **Dry run mode**: classify photos without writing anything to Immich, for testing.

## Requirements

- Immich running via Docker Compose
- Docker on the same host
- An Immich API key with the following permissions:

  | Permission | Reason |
  |---|---|
  | `asset.read` | Search results and asset metadata |
  | `asset.view` | Loading thumbnails |
  | `person.create` | Creating a new pet as a person in Immich |
  | `person.read` | Reading existing persons and thumbnails |
  | `person.update` | Renaming a pet |
  | `person.delete` | Deleting a pet |
  | `person.reassign` | Assigning a face to a person |
  | `face.create` | Writing face entries (the actual tagging) |
  | `face.read` | Checking existing faces on an asset |
  | `face.delete` | Removing face entries on ref removal or pet deletion |

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/tedornitier/immich-pet-tagger
cd immich-pet-tagger
```

### 2. Find your Immich Docker network

```bash
docker network ls
```

Look for a network with "immich" in the name (e.g. `immich_default`).

### 3. Configure docker-compose.yml

Edit the following values:

```yaml
environment:
  - IMMICH_URL=http://immich-server:2283   # container-to-container URL
  - IMMICH_API_KEY=your_api_key_here
  - IMMICH_EXTERNAL_URL=http://localhost:2283  # browser-facing URL

networks:
  immich_default:          # match your actual network name
    external: true
```

### 4. Start the container

```bash
docker compose up -d
docker compose logs -f   # watch startup logs
```

On first start, the CLIP model (~350 MB) is downloaded and cached inside the container. Subsequent starts are fast.

### 5. Open the UI

Go to **http://localhost:8000** in your browser.

---

## Enrolling your pets

### Option A: import from Immich

If Immich already recognizes your pet as a person (from its own face detection):

1. Click **Import from Immich** in the sidebar
2. Select your pet from the grid
3. Enter a description (e.g. "orange tabby cat") and optional date range
4. Click **Import** — up to 20 reference photos are fetched automatically

### Option B: add manually

1. Click **+ Add pet**, enter a name, description, and optional date range
2. Click **Find similar photos** — results are ranked by how closely they match your description
3. Select good reference photos and click **Add to pet**
4. Aim for 10–20 references showing different angles, lighting, and distances

### Picking good reference photos

Good references are the single biggest factor in accuracy. When selecting refs:

- **Skip photos with multiple pets**: YOLO crops to the highest-confidence animal, so you can't control which pet gets embedded. Use single-pet photos instead.
- **Skip blurry or low-light photos**: CLIP embeddings are less reliable when the subject isn't clearly visible.
- **Skip uncertain ones**: if you're not sure whether it's your pet, skip it. Noise in refs hurts more than a smaller ref count.
- **Vary the shots**: different angles, distances, and lighting conditions generalize better than many similar-looking photos.

### Adding negative samples

Negative samples help the classifier reject photos that look like your pet but aren't. More negatives = fewer false positives.

1. In the "Not my pets" panel, click **Find candidates** — this searches across all your pets and surfaces the most confusable photos
2. Select photos that are not your pet (other animals, stuffed toys, similar-looking subjects)
3. Click **Mark selected as "not my pets"**
4. Aim for roughly 2–3x as many negatives as total references across all pets

### Verifying

After the next poll cycle (within 5 minutes), your pet should appear in Immich's **People** section. Click **Tagged** next to any pet to see which photos have been tagged.

---

## Backfilling old photos

By default the poller only processes photos taken after the container first started. To tag existing photos, set the scan date in the sidebar to an earlier date and click **Apply**.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `IMMICH_URL` | `http://immich-server:2283` | Immich URL for container-to-container communication |
| `IMMICH_EXTERNAL_URL` | `http://localhost:2283` | Immich URL as seen from your browser, used for links |
| `IMMICH_API_KEY` | required | Immich API key |
| `POLL_INTERVAL` | `300` | Seconds between scans |
| `THRESHOLD` | `0.8` | Min confidence (0–1) to tag a photo |
| `DRY_RUN` | `false` | Classify but do not write to Immich |
| `CLIP_MODEL` | `ViT-B-16` | CLIP model name (matches Immich default) |
| `CLIP_PRETRAINED` | `openai` | CLIP pretrained weights |

---

## GPU support

The default `docker-compose.yml` is configured for NVIDIA GPUs. If you don't have one, remove the `deploy` section and change `CUDA: "true"` to `"false"` in the build args, then rebuild.

If you do have an NVIDIA GPU, make sure the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) is installed and Docker is using the WSL2 backend (Windows) or the native toolkit (Linux).

## Limitations

- **YOLO fallback**: when no animals are detected by YOLO, the full image is classified as a whole and only one pet can be tagged per photo
- **Polling only**: photos are processed within 5 minutes of upload, not instantly

## Troubleshooting

**Pet not appearing in Immich after enrollment**
Immich only shows people with at least one face assigned. Add at least one reference photo and wait for a poll cycle.

**Low accuracy / wrong pet tagged**
Add more reference photos, add more negative samples, or lower the threshold in `docker-compose.yml`.

**Container can't reach Immich**
Make sure the network name in `docker-compose.yml` matches the output of `docker network ls`.

**Thumbnail proxy returns 401**
Your API key is missing `asset.view` permission.
