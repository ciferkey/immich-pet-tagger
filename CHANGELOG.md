# Changelog

## v1.3.2

### Fixes
- Manual scans now use EXIF date for the date picker, matching what users see in the Immich library. The background poller continues using upload time to avoid skipping late-synced photos.

---

## v1.3.1

### Fixes
- CPU image reduced from ~12.7 GB to ~2.4 GB. The default build was silently pulling the CUDA torch wheel from PyPI instead of the CPU-only wheel.
- Switched to a multi-stage Docker build with a virtual environment, eliminating ghost install layers that kept deleted packages on disk.
- Removed triton (545 MB, only needed for `torch.compile()`) from all images.
- Fixed a duplicate opencv library left behind by the previous `--force-reinstall` approach.

---

## v1.3.0

### Crop-centric references
- References now store the YOLO-detected crop rather than the full image, matching the format seen during inference. Existing refs are migrated automatically on startup.
- The ref grid now shows the crop thumbnail instead of the full photo.
- Import from Immich and Find references now run YOLO on candidates and skip photos where no animal is detected.
- When YOLO finds no crop for a ref, the full image is used as a fallback instead of silently dropping the ref.

### UX improvements
- Updated getting started guide with guidance based on tested results: aim for 20–30 references, ~50 negatives. Action buttons now have matching tooltips. Skip renamed to Ignore.
- Refs panel no longer scrolls to the top when removing a ref.
- Fixed an intermittent bug where clicking a pet in the sidebar would show the getting started guide instead of the pet view.
- Find candidates now samples 50 photos instead of 100 (faster) and raises the minimum score threshold to 30%.

### Other
- Scan progress now uses upload time (`createdAt`) instead of EXIF date, so late-synced photos never fall behind the scan cutoff.
- Ref and negative grid thumbnails now use `object-fit: contain`.
- Default UI port changed to 2287.

---

## v1.2.0

### Features
- **Version indicator**: the header now shows the current version and notifies you when a new release is available.
- **Persistent model cache**: the YOLO and CLIP models are cached in a named Docker volume, so they survive container updates without re-downloading.

### Fixes
- **CPU-only default**: `docker-compose.yml` now defaults to the `:cpu` image with GPU acceleration as an opt-in. Uncomment the `deploy` block and switch to the `:latest` image to enable NVIDIA GPU.
- **UI bound to localhost**: the UI now binds to `127.0.0.1` by default, preventing unauthenticated access from other devices on the network.
- **Low confidence count**: the "Review N low confidence" count in scan results now matches the number of photos actually shown in the review panel.
- **Not my pets count**: the label now always shows a plain number instead of extra status text.
- **UI font alignment**: minor font consistency fix.

---

## v1.1.0

### Fixes
- **Borderline threshold**: the low-confidence review threshold was hardcoded to 0.85 instead of tracking the configured `THRESHOLD`. Now derived dynamically as `[THRESHOLD * 0.375, THRESHOLD]`.
- **Import from Immich: missing face IDs**: refs imported via "Import from Immich" had `face_id: null`, preventing clean deletion later. Face IDs are now extracted during import.

### Docs
- Pre-built images published to GHCR for NVIDIA (`:latest`), AMD/ROCm (`:rocm`), and CPU-only (`:cpu`). No build step needed.
- Clarified that the tagger can run on a separate machine from Immich. Set `IMMICH_URL` to Immich's IP or hostname and change `external: true` to `external: false` at the bottom of `docker-compose.yml`.
- Added update instructions: `docker compose pull && docker compose up -d`.

---

## v1.0.0

### Performance
- **Batch GPU inference**: CLIP workers dequeue requests from all scan threads and process them as batches, keeping the GPU fully utilised. Scans are significantly faster on large libraries.
- **Concurrent thumbnail fetching**: assets are fetched in parallel with a configurable thread pool (`SCAN_WORKERS`, auto-derived as `GPU_WORKERS × 32`).
- **YOLO runs on CUDA**: the YOLO detector now loads onto the GPU when available.

### Features
- **AMD/ROCm support**: Docker image supports NVIDIA (default), AMD/ROCm, and CPU-only via a build arg.
- **In-UI getting started guide**: the main panel shows a 6-step workflow on first open. The `i` button in the sidebar header brings it back at any time.
- **Pet folder keys use person ID**: pet data folders are keyed by Immich person UUID instead of name, avoiding issues with special characters.

---

## v0.4.0

### Features
- **YOLO animal detection**: bounding boxes are computed before classification. Multi-pet photos are handled per crop, so each animal in the frame is classified separately. Improves accuracy and enables tagging photos with more than one pet.
- **Visual ref search**: "Find similar photos" now uses ref asset images as the CLIP query instead of text description, producing far more relevant candidates.
- **Find missed photos**: new "Find missed" button scores borderline candidates with the classifier and surfaces photos just below the confidence threshold. Useful for finding good refs to improve recall.
- **Score-based negatives calibration**: "Find not my pets" uses the classifier to exclude photos the model already considers pets, ensuring only genuinely ambiguous photos are surfaced as negatives.
- **Live scan**: the scan panel shows live per-category counts (Tagged, Low conf., Other, Already tagged) and the current photo date while scanning. Triggering a new scan cancels any in-progress one.
- **Review low confidence**: after a scan, a new "Review X low confidence" button lists photos the classifier identified as a pet but scored below threshold, sorted by score with color-coded badges.
- **Open in Immich**: each thumbnail in all photo grids now has a direct link icon to open the asset in Immich.
- **Embedding cache persisted to disk**: CLIP embeddings are saved to `data/embeddings.pkl` and reloaded on startup, so restarts no longer re-embed all ref and negative photos.

### Fixes
- **Critical: duplicate face prevention**: `"person": null` faces (Immich-detected but unassigned) caused the existing-faces check to throw and return an empty set, bypassing deduplication and creating duplicate tags. Fixed with a null guard in all face lookup paths.
- **Pagination cap removed**: asset search was silently stopping at 1000 results due to a `total` field that Immich caps at 1000. Now paginates correctly until the page is smaller than the page size.
- **Scan dedup across YOLO crops**: a person was not marked as tagged when face_id retrieval failed after a successful POST, causing the same pet to be re-tagged on subsequent crops of the same photo.
- **Pet delete**: per-ref face deletion loop removed. Deleting the Immich person cascades face removal automatically.
- **activePet stale reference**: "Find missed" button stayed disabled after re-enrollment because the in-memory pet reference was not refreshed after `loadPets()`.
- Responsive photo grid (auto-fill columns instead of fixed 4).
- Browser locale used for date formatting in tooltips and date inputs.

### Docs
- README rewritten with features overview, docker-compose setup, and enrollment tutorial.
- Added guidance on picking good reference photos (skip multi-pet, blurry, or ambiguous frames).

---

## v0.3.0

### Features
- **Import from Immich**: import a pet directly from an existing Immich person. The tool fetches up to 20 evenly distributed single-pet photos as refs automatically, skipping photos where multiple named people appear.
- **Find candidates for "not my pets"**: new button searches across all pets simultaneously, merges results, scores them by pet-likeness using the classifier, and shows the top 60 in the main grid for bulk review.
- **Tool-only delete**: when deleting a pet, a third option lets you remove it from the tool only (keeping the Immich person and all tagged photos intact). Assets are never deleted in either case.
- **Clear all refs / Clear all negatives**: bulk-clear buttons with confirmation, local only, no Immich changes.

### Fixes
- "Find similar photos" now skips the CLIP classifier stage when the pet has no refs, returning text search results immediately instead of waiting for model inference.
- Auto-select newly created pet after adding it.
- Stay on the edited pet after saving edits (was jumping to the first pet).
- Import no longer crashes on photos where Immich returns a null person in the faces list.

### Internal
- `app/` directory volume-mounted in docker-compose: Python, HTML, CSS, and JS changes apply after `docker compose restart` with no rebuild needed.

---

## v0.2.0

### Features
- **Similar photo suggestions**: new "Find similar photos" button runs a two-stage pipeline — Immich smart search pre-filters by pet description, then the classifier ranks candidates by pet class probability. Replaces the manual search bar.
- **Pet description field**: each pet now has a short description (2-4 keywords, e.g. "orange tabby cat") used as the CLIP text query for suggestions.
- **Shift+click range selection**: hold Shift and click a second photo to select the full range in the grid.
- **Tagged photos panel**: view photos already tagged for the active pet; remove a tag or mark as "not my pets" in bulk.
- **Scan status panel**: shows last scan time, badge (running/idle/error/never), and per-pet stats (tagged, skipped, errors) after each poll cycle.
### UI
- Refs and "Not my pets" grids: 3-column layout, equal height, independently scrollable.
- "Not my pets" panel (formerly Negatives): unified card style with refs, clickable thumbnails linking to Immich.
- "Find similar photos" button moved to the top of the right panel as the primary action.
- Scan and last scan controls moved to the bottom of the sidebar.
- Removed the manual search bar.

### Internal
- Negative samples subsampled to 3x pet refs in the classifier to keep class balance without discouraging large negative sets.
- Static files split into `style.css` and `app.js` (was a single `index.html`).
- Backend refactored: `data.py` for file I/O, `immich.py` for HTTP helpers (replaces `immich_apis.py`).

---

## v0.1.0

Initial release. Core tagging loop, pet enrollment UI, ref/negative management, logistic regression classifier on CLIP embeddings.
