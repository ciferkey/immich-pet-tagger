# immich-pet-tagger

Automatic pet tagging for Immich. Identifies your pets in new photos and tags them as people in Immich, the same way Immich tags human faces, but for cats, dogs, or any visually distinct subject.

Uses CLIP embeddings and a few reference photos you provide. No cloud services, no training required, runs entirely on your own hardware as a Docker sidecar alongside Immich.

## How it works

1. You enroll your pets via a web UI: search your Immich library, pick reference photos, and assign them to a named pet
2. A classifier is trained locally from those references using CLIP
3. Every 5 minutes, new photos are classified and matching pets are tagged in Immich
4. Pets appear in Immich's People section just like humans

## Requirements

- Immich running via Docker Compose
- Docker on the same host
- An Immich API key with the following permissions (no others needed):

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

### 1. Create the data directory

```
mkdir C:\Users\yourname\immich-pet-tagger\data      # Windows
mkdir /home/yourname/immich-pet-tagger/data         # Linux/macOS
```

This is where pet references, config, and state files will be stored.

### 2. Find your Immich Docker network

```bash
docker network ls
```

Look for a network with "immich" in the name (e.g. `immich_default`).

### 3. Configure docker-compose.yml

Edit the following in `docker-compose.yml`:

```yaml
environment:
  - IMMICH_URL=http://immich-server:2283   # or your Immich container name
  - IMMICH_API_KEY=your_api_key_here

volumes:
  - C:/Users/yourname/immich-pet-tagger/data:/data   # Windows
  # - /home/yourname/immich-pet-tagger/data:/data    # Linux/macOS

networks:
  immich_default:                          # match your actual network name
    external: true
```

### 4. Start the container

```bash
docker compose up -d
docker compose logs -f   # watch startup logs
```

On first start, the CLIP model (~350MB) is downloaded and cached. Subsequent starts are fast.

### 5. Enroll your pets

Open **http://localhost:8000** in your browser.

1. Click **+ Add pet**, give it a name and optionally a date range (useful for pets no longer around, or recently acquired)
2. Search your Immich library using natural language (e.g. "black cat on sofa")
3. Click photos to select them, then **Add to pet →**
4. Repeat until you have 10–20 good reference photos per pet
5. The classifier trains automatically on the next poll cycle

### 6. Verify

After the next poll cycle (within 5 minutes), your pet should appear in Immich's **People** section. New photos are tagged automatically as they're uploaded.

---

## Enrollment tips

- **More references = better accuracy.** Aim for 10–20 photos per pet showing different angles, lighting, and ages
- **Date ranges** help when a pet is only in photos from a certain period. Set "since" and "until" to avoid misclassification
- **Negative samples**: deselect any active pet, search for things that aren't your pet (other cats, dogs, random photos), select them and click "Mark as unknown". This improves the classifier's ability to reject non-pet photos
- The **confidence threshold** (default 0.92) is intentionally high to avoid false positives. Lower it in `docker-compose.yml` if your pet is being missed

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `IMMICH_URL` | required | Base URL of your Immich instance |
| `IMMICH_API_KEY` | required | Immich API key |
| `IMMICH_EXTERNAL_URL` | `http://localhost:2283` | Immich URL as seen from your browser, used for links |
| `POLL_INTERVAL` | `300` | Seconds between scans for new photos |
| `THRESHOLD` | `0.92` | Min confidence (0–1) to tag a photo |
| `DRY_RUN` | `false` | Classify but don't write to Immich |
| `CLIP_MODEL` | `ViT-B-16` | CLIP model name (matches Immich default) |
| `CLIP_PRETRAINED` | `openai` | CLIP pretrained weights |

## Backfilling old photos

By default the poller only processes photos taken after the container first started. To tag existing photos, reset the timestamp:

```bash
# Windows
echo 2020-01-01T00:00:00.000Z > C:\Users\yourname\immich-pet-tagger\data\last_scan_timestamp.txt

# Linux/macOS
echo "2020-01-01T00:00:00.000Z" > /home/yourname/immich-pet-tagger/data/last_scan_timestamp.txt
```

Then restart the container. Note: large libraries will take time to process on CPU.

## Limitations & roadmap

- **One pet per photo**: when multiple pets appear in the same photo, only the highest-confidence match is tagged. YOLO-based multi-pet detection is planned for v1.1
- **Polling only**: photos are processed within 5 minutes of upload. Native event-based triggering will be added when Immich's Workflows feature ships
- **Search pagination**: search results are capped at 60 photos. Full pagination is planned for v1.1
- **CPU only**: CLIP runs on CPU by default. If CUDA is available it will be used automatically, but GPU support is not officially tested

## Troubleshooting

**Pet not appearing in Immich after enrollment**
Immich only shows people with at least one face assigned. Add at least one reference photo and wait for a poll cycle.

**Low accuracy / wrong pet tagged**
Add more reference photos, add negative samples, or lower the threshold slightly.

**Photos from before my pet's date range are being tagged**
Check that `since`/`until` are set correctly in the edit modal. The poller respects these ranges.

**Container can't reach Immich**
Make sure the network name in `docker-compose.yml` matches the output of `docker network ls`.

**Thumbnail proxy returns 401**
Your API key is missing `asset.view` permission.
