---
name: shutterstock2kml
description: Search Shutterstock for photos by queries, resolve each photo to a real Google Maps place via Playwright (Chromium) — extracting name, coordinates, rating and review count in one shot — and generate a styled KML file with colored icons by visit-worthiness.
user_invocable: true
---

# shutterstock2kml

Convert Shutterstock image search results into a KML file with place ratings, coordinates, and styled icons.

Working directory: `/Users/alekseygrachev/git/shutterstock2kml`

## Stage 1: Generate queries.json

Create `queries.json` with an array: ["Romania"]

Ask the user what region/topic to search for if not obvious from context.

## Stage 2: Shutterstock scraping -> places.json

Scrape Shutterstock search results with **patchright** (a Playwright fork that
patches automation leaks) + **system Chrome** (`channel="chrome"`) + a
**persistent profile** + **headful mode**. This combo defeats DataDome — plain
Playwright (even with `playwright-stealth`) is reliably blocked, and the
official API requires paid keys we no longer use.

### Setup

```
.venv/bin/pip install patchright
# patchright reuses Playwright's binary; no extra install. System Chrome must
# be installed at /Applications/Google Chrome.app (macOS) or `google-chrome` in PATH (Linux).
```

### Per-page flow

For each query in `queries.json`:
1. Warm up DataDome cookies — visit `https://www.shutterstock.com/` first, mouse-jiggle, wait ~2.5s.
2. Navigate to `https://www.shutterstock.com/search/<q>?image_type=photo` (page 1) or `…?image_type=photo&page=N` (N>1). `?page=1` gets normalised to the bare URL and triggers `ERR_ABORTED`, so omit it.
3. Poll `main img[alt]` up to ~50s for first content; then scroll 12 × `window.innerHeight` to trigger lazy load.
4. Extract `{description, preview_url}` from `main img[alt]` — `description = img.alt`, `preview_url = img.currentSrc || img.src`. Drop items where alt < 4 chars or src starts with `data:`.

Important: do **not** filter by `img.closest('a[href*="/image-photo/"]')` — Shutterstock places the anchor as a sibling, not an ancestor, so `closest()` returns null and you get zero results. Iterate `main img[alt]` directly.

Reuse the same browser context across all pages and queries — re-warming each time looks more bot-like and is wasteful.

Save as `places.json` — array of objects:
```json
[
  {
    "description": "...",
    "preview_url": "https://www.shutterstock.com/image-photo/...-260nw-...jpg",
    "keywords": [],
    "categories": []
  }
]
```

`keywords` / `categories` come back empty (the search results page doesn't expose them — would require clicking each photo). Stage 4 (KML) handles the empty case via `or []`.

## Stage 2b: LLM filtering -> places_filtered.json + places_dropped.json

Read `places.json` and analyze each entry. Keep ONLY entries that refer to **specific, real, visitable geographic places** (landmarks, buildings, natural sites, cities, etc.).

Discard entries that are:
- Generic landscapes without identifiable location
- Studio shots, portraits, food photos
- Abstract or artistic images

Also strip trailing `Stock Photo` / `Stock Image` suffixes Shutterstock attaches to alt text.

Write **both**:
- `places_filtered.json` — entries that passed the filter
- `places_dropped.json` — entries that were filtered out (same shape as input, with the suffix-stripped description), so the decisions can be audited / refined.

## Stage 3: Resolve places via Playwright -> places_rated.json

Input: `places_filtered.json` if present, else `places.json` (the resolver auto-falls-back).

Use **Playwright (Chromium, headless)** to query Google Maps with each entry. Google Maps acts as **filter + geocoder + rating source** all at once: a single page render gives us name, coordinates, rating and review count. No Nominatim, no separate LLM filter.

### Setup

```
python3 -m venv .venv
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
```

### Per-entry fetch

For each Shutterstock entry, build a search query from the description (append the target country/region — e.g. " Romania" — to disambiguate). Navigate to:
```
https://www.google.com/maps/search/<URL-encoded query>
```

Wait up to ~10s for the place card (`div.F7nice`) to render, then read everything from the page in one `page.evaluate`:

```javascript
() => {
  const root = document.querySelector('[role="main"]') || document.body;
  const titleEl = root.querySelector('h1.DUwDvf, h1[class*="DUwDvf"]');
  const node = root.querySelector('div.F7nice, div[class*="F7nice"]');
  if (!node || !titleEl) return null;
  const ratingEl = node.querySelector('span[aria-hidden="true"]');
  const countEl  = node.querySelector('span[aria-label*="review"]');
  return {
    place_name:  titleEl.innerText.trim(),
    rating_text: ratingEl ? ratingEl.innerText.trim() : null,
    count_label: countEl  ? countEl.getAttribute('aria-label') : null,
  };
}
```

Then extract `lat` / `lng` from the URL Google Maps redirects to after a successful resolution. Two URL shapes are seen in the wild — match both, **`!3d!4d` first** since it appears alongside the place card while `@…` may still reflect the old viewport:
- `…/@<LAT>,<LNG>,<ZOOM>z/…`  → regex `@(-?\d+\.\d+),(-?\d+\.\d+)`
- `…!3d<LAT>!4d<LNG>…`         → regex `!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)`

The URL update can lag behind the card render — poll `page.url` for ~5s after the card is found.

- `rating` = `parseFloat(rating_text)` (may be null)
- `review_count` = digits extracted from `count_label` (e.g. `"73,417 reviews"` -> `73417`; may be null)
- `place_name` = `titleEl.innerText` — the official Google Maps name (often differs from the keyword we searched with — that's fine, this is the canonical name)
- If `div.F7nice` / `h1.DUwDvf` never render within the timeout, **drop the entry** — Google Maps couldn't resolve a real place, so the Shutterstock photo isn't a visitable location for our purposes.

This single step replaces what used to be three separate stages (LLM filtering, Nominatim geocoding, rating lookup).

### Parallelization

Use **async Playwright** (`playwright.async_api`) with N parallel browser contexts under one shared `chromium.launch(headless=True)`. Default N = 12 (override via `STAGE3_WORKERS` env var). **Do not** use `ThreadPoolExecutor` with sync Playwright — it crashes (greenlet thread error). Either go fully async, or run sync Playwright in subprocess workers.

User-Agent override is recommended (some networks block default Playwright UA):
```
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
```

### Deduplication

Google Maps will resolve many Shutterstock photos to the same canonical place (e.g. dozens of Bran Castle shots). Deduplicate by `place_name` after resolution, keeping the entry whose preview / description is most informative.

### Output

Set `google_maps_link` to `https://www.google.com/maps/search/<URL-encoded place name>` for the human user.

Save as `places_rated.json`:
```json
[
  {
    "place_name": "Peleș Castle",
    "lat": 45.3601,
    "lng": 25.5427,
    "rating": 4.7,
    "review_count": 73417,
    "google_maps_link": "https://www.google.com/maps/search/Peles+Castle",
    "description": "...",
    "preview_url": "https://...",
    "keywords": [],
    "categories": []
  }
]
```

`keywords`/`categories` are usually empty (we no longer use the API). Stage 4 falls back to `place_name + description` for category detection.

## Stage 4: Generate result.kml

Generate `result.kml` from `places_rated.json`.

### Filter by rating

Drop entries with `rating < 3.8`. Entries where `rating is None` (Google Maps card had no rating) pass through.

### Icon color logic (popularity by review count)

Based on Google Maps `review_count`:
- **Green**: `review_count > 1000`
- **Yellow**: `100 ≤ review_count ≤ 1000`
- **Red**: `review_count < 100` OR `review_count is None`

### Category detection

Determine category from `place_name + description + keywords + categories` (the latter two are usually empty after Stage 2 was migrated off the API):
- `castle` / `fortress` / `palace` -> castle
- `monastery` / `church` / `cathedral` / `temple` -> religious
- `mountain` / `lake` / `waterfall` / `cave` / `gorge` / `nature` / `park` -> nature
- `museum` / `gallery` -> museum
- `city` / `square` / `bridge` / `architecture` -> urban
- Default -> default

### KML Styles

Define styles for each color+category combination. Use Google Earth KML icons:

| Category | Icon |
|----------|------|
| castle | `http://maps.google.com/mapfiles/kml/shapes/ranger_station.png` |
| religious | `http://maps.google.com/mapfiles/kml/shapes/worship_general.png` |
| nature | `http://maps.google.com/mapfiles/kml/shapes/parks.png` |
| museum | `http://maps.google.com/mapfiles/kml/shapes/museum.png` |
| urban | `http://maps.google.com/mapfiles/kml/shapes/homegardenbusiness.png` |
| default | `http://maps.google.com/mapfiles/kml/paddle/COLORABBREV-circle.png` |

For default icons use: `grn-circle.png`, `ylw-circle.png`, `red-circle.png`.

For category icons, apply KML color overlay (KML uses aaBBGGRR format):
- Green: `ff00ff00`
- Yellow: `ff00ffff`
- Red: `ff0000ff`

Scale: green=1.2, yellow=1.0, red=0.8

Style IDs follow the pattern: `#COLOR-CATEGORY` (e.g. `#green-castle`, `#yellow-nature`, `#red-default`).

### KML Structure

Flat list — no `<Folder>` grouping. Sort placemarks by `review_count` desc, entries with no `review_count` go to the end.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Shutterstock Places: QUERY_REGION</name>

  <!-- All Style definitions here (3 colors x 6 categories = 18 styles) -->

  <!-- All Placemarks at top level, sorted by review_count desc -->
</Document>
</kml>
```

### Placemark template

```xml
<Placemark>
  <name>PLACE_NAME</name>
  <styleUrl>#COLOR-CATEGORY</styleUrl>
  <Point>
    <coordinates>LNG,LAT,0</coordinates>
  </Point>
  <description><![CDATA[
    <h3><a href="GOOGLE_MAPS_LINK">PLACE_NAME on Google Maps</a></h3>
    <p>Rating: RATING/5</p>            <!-- omit "(N reviews)" if review_count is null -->
    <p><img src="PREVIEW_URL" width="400" /></p>
    <p><em>DESCRIPTION</em></p>
  ]]></description>
</Placemark>
```

Sort all placemarks by `review_count` desc; entries without review_count go to the end.

## Verification

After generating `result.kml`:
1. Validate XML: `python3 -c "import xml.etree.ElementTree as ET; ET.parse('result.kml'); print('Valid XML')"`
2. Report summary: total places, green/yellow/red counts, categories breakdown
