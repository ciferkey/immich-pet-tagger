# Changelog

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
