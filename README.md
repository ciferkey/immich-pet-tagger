# immich-pet-tagger

Automatic pet tagging for Immich. Identifies your pets in new photos and tags them as people in Immich, the same way Immich tags human faces, but for cats, dogs, or any visually distinct subject.

Uses CLIP embeddings and a few reference photos you provide. No cloud services, no training required, runs entirely on your own hardware as a Docker sidecar alongside Immich.

![Pet Tagger UI showing a pet's possible missed photos and a past scan result](screenshot.png)

## How it works

1. You enroll your pets via a web UI: provide a few reference photos and a short description
2. A logistic regression classifier is trained locally on CLIP embeddings of those references
3. Every 5 minutes, new photos are classified and matching pets are tagged in Immich
4. Pets appear in Immich's People section just like humans

## Features

- **Import from Immich**: if Immich already recognizes your pet as a person, import them in one click. The tool picks up to 20 evenly distributed reference photos automatically.
- **Find similar photos**: uses a two-stage search to surface candidates. Your reference photos are used as visual queries against Immich's smart search, and the local classifier re-ranks the results by pet probability. Falls back to text search using your description when no refs exist yet.
- **Find candidates for "not my pets"**: samples random photos from your library, scores them with the classifier, and surfaces the top 60 most likely to confuse it for bulk review.
- **Negative samples**: mark photos that look like your pet but aren't, to sharpen the classifier's ability to reject false positives.
- **Date ranges**: restrict a pet to photos taken within a specific period (useful for pets that have passed away or were adopted later).
- **Scan controls**: set the scan start date and trigger a scan from the sidebar; the last scan stats are shown live.

## Requirements

- Immich running and reachable over HTTP (tested with v2.7.5)
- Docker (on the same host or any machine that can reach Immich on the network)
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

### 2. Configure docker-compose.yml

Edit the following values:

```yaml
environment:
  - IMMICH_URL=http://immich-server:2283     # how this container reaches Immich
  - IMMICH_API_KEY=your_api_key_here         # generate one in Immich: Account Settings → API Keys
  - IMMICH_EXTERNAL_URL=http://localhost:2283 # how your browser reaches Immich (for photo links)
```

**Same Docker host as Immich:** use the container name as the hostname (e.g. `http://immich-server:2283`) and keep the shared network section at the bottom of the file. Find your Immich network name with `docker network ls`.

**Immich on a separate machine:** use its IP or hostname instead (e.g. `http://192.168.1.100:2283`) and change `external: true` to `external: false` at the bottom of `docker-compose.yml`.

### 4. Start the container

The default configuration runs on CPU, which works for any machine without extra setup.

```bash
docker compose up -d
docker compose logs -f   # watch startup logs
```

If you want GPU acceleration, see [GPU support](#gpu-support) before running.

On first start, the YOLO model (~6 MB) and CLIP model (~350 MB) are downloaded and cached. Subsequent starts are fast.

### 5. Open the UI

Go to **http://localhost:2287** in your browser.

The UI binds to `127.0.0.1` by default, so it is only reachable from the same machine. There is no authentication. To allow access from other devices on your network, or to use a different port, change the port binding in `docker-compose.yml`:

```yaml
ports:
  - "0.0.0.0:2287:8000"  # accessible from other devices on your network
```

To use a different port, change only the first number. The second number (`8000`) is the container's internal port and must stay as-is:

```yaml
ports:
  - "127.0.0.1:9000:8000"  # serves on port 9000 instead
```

Do not expose this to the internet without putting an authenticated reverse proxy in front of it.

## Updating

To update to a new version, pull the latest image and restart:

```bash
docker compose pull
docker compose up -d
```

This works for all variants since pre-built images are published for NVIDIA, AMD, and CPU-only.

---

## Getting started

Getting good results takes a few iterations. Start by adding a pet, building up references, and adding some negatives. Run a short test scan, review the results, refine, and repeat until you're satisfied. Then run the full backfill.

### Step 1: Add your pet

**Import from Immich**: use this if Immich already recognizes your pet as a person from its own face detection. This is ideal when the person in Immich contains only photos of that pet, for example if you tagged them manually and are confident the assignments are correct. The tagger does not remove or correct existing Immich face assignments, so any misidentified photos already tagged in Immich will stay tagged. If Immich's recognition was noisy, consider adding your pet manually instead.

1. Click **↓ Import from Immich** in the sidebar
2. Find and click your pet in the grid
3. Enter a short description (e.g. `orange tabby cat`) and an optional date range
4. Click **Import**. Up to 20 reference photos are imported automatically.

**Add manually**: use this if Immich doesn't know your pet yet.

1. Click **+ Add pet**, fill in the name, a short description (e.g. `black labrador dog`), and an optional date range
2. Click **Create**

The description is used by Immich's CLIP model to find the first batch of candidate photos. Keep it short: 2–4 descriptive keywords.

### Step 2: Add reference photos

References are what the classifier learns from. Quality matters more than quantity.

1. Select your pet in the sidebar and click **Find references**
2. Browse the results. They are ranked by visual similarity to your existing refs, or to your description if no refs exist yet.
3. Aim for 20–30 to start; results improve up to around 50. For each photo:
   - **Add to pet**: clear, close-up shot, your pet is the only subject.
   - **Ignore**: blurry, distant, another person or animal visible alongside your pet, or a look-alike that is not yours. Ignored photos won't appear again.
   - **Not my pets**: photos that could confuse the classifier. Empty rooms, other species, ambiguous shots. Around 50 is enough.

### Step 3: Add "not my pets" samples

These teach the classifier what not to tag: empty rooms, other animals of a different species, ambiguous shots with no clear subject. Without them, the classifier will tag almost anything.

1. In the **Not my pets** panel (bottom right of the screen), click **Find candidates** to automatically surface more photos that might confuse the classifier
2. Select the relevant ones and click **Not my pets**


### Step 4: Run a test scan

Start with a recent date so the scan covers fewer photos, making it quicker to review and refine before committing to a full backfill.

1. In the **Scan from** panel at the bottom of the sidebar, set a date 1–2 weeks back
2. Click **Scan** and wait for the results
3. If **Review N low confidence** appears in the results, click it to see photos the classifier identified as a match but wasn't fully confident about.
4. Go through them: add correctly identified ones as references, and click **Ignore** on the rest. Ignored photos won't appear in future results.

### Step 5: Iterate

Repeat steps 2–4 a couple of times. Each round of added references and negatives improves accuracy. Results typically stabilize after 2–3 iterations.

### Step 6: Run the full backfill

Once you're happy with the accuracy on the test window:

1. Set the scan date to the earliest date you want to tag. A good starting point is the date you got your pet.
2. Click **Scan** to process all photos in that range

After that, the background poller runs every 5 minutes and tags new photos automatically. Your pets appear in Immich's **People** section.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `IMMICH_URL` | `http://immich-server:2283` | Immich URL for container-to-container communication |
| `IMMICH_EXTERNAL_URL` | `http://localhost:2283` | Immich URL as seen from your browser, used for links |
| `IMMICH_API_KEY` | required | Immich API key |
| `POLL_INTERVAL` | `300` | Seconds between scans |
| `SCAN_WORKERS` | `GPU_WORKERS × 32` | Concurrent thumbnail fetches. Auto-derived to keep GPU batches full. Override only if Immich feels slow during scans. |
| `GPU_WORKERS` | `2` (GPU) / `1` (CPU) | Parallel YOLO and CLIP inference threads. `2` is optimal for GPU; CPU defaults to `1` since a second worker just duplicates the models in RAM with no throughput gain. |
| `YOLO_INPUT_SIZE` | `640` | YOLO detection resolution in pixels. Higher values improve detection of small animals at the cost of more memory and compute. Must be a multiple of 32. |
| `YOLO_BATCH_SIZE` | `32` | Max images per YOLO inference batch. Reduce if you hit GPU out-of-memory errors. |
| `THRESHOLD` | `0.8` | Min confidence (0–1) to tag a photo |

---

## GPU support

The default setup runs on CPU and requires no extra configuration. A GPU makes scans significantly faster but requires additional setup. Pre-built images are published for all three variants.

**CPU (default):** no changes needed.

**NVIDIA GPU:** install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on your host, then in `docker-compose.yml`:
1. Change the image tag to `:latest`
2. Uncomment the `deploy:` section
3. Set `GPU_WORKERS=2`

```yaml
image: ghcr.io/tedornitier/immich-pet-tagger:latest
```

The published image works on Maxwell, Pascal, Volta, Turing, Ampere, Ada Lovelace, and Hopper GPUs (compute capability 5.0–9.0, e.g. GTX 9xx/10xx, RTX 20xx/30xx/40xx, Tesla P/V/A/H100). Blackwell (RTX 50xx) is not covered by the default image; build from source with a newer CUDA wheel if needed.

**AMD GPU:** install ROCm drivers, then in `docker-compose.yml`:
1. Change the image tag to `:rocm`
2. Uncomment the `deploy:` section and change the driver to `amdgpu`

```yaml
image: ghcr.io/tedornitier/immich-pet-tagger:rocm
```
```yaml
driver: amdgpu
```

CPU-only works fine for most home libraries. Expect roughly 10x slower processing compared to GPU.

**Unsupported:** Pascal GPUs (GTX 1070, 1080, etc., compute capability sm_61) are not supported by the `:latest` image. Use `:cpu` instead.

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
