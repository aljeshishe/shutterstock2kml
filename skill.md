---
name: shutterstock2kml
description: Search Shutterstock for photos by queries, find place ratings via web search, geocode with Nominatim, and generate a styled KML file with colored icons by visit-worthiness.
user_invocable: true
---

# shutterstock2kml

Convert Shutterstock image search results into a KML file with place ratings, coordinates, and styled icons.

Working directory: `/Users/alekseygrachev/git/shutterstock2kml`

## Stage 1: Generate queries.json

Create `queries.json` with an array of search queries. Start with a reasonable set of specific queries for the target region. Example for Romania:

```json
["Romania landmarks", "Romania castles", "Romania monasteries", "Transylvania", "Bucharest architecture", "Romanian Carpathians"]
```

Ask the user what region/topic to search for if not obvious from context.

## Stage 2: Shutterstock API -> places.json

Read `.env` file for `SHUTTERSTOCK_API_KEY` and `SHUTTERSTOCK_API_SECRET`.

For each query in `queries.json`, call the Shutterstock API using Bash:

```
source .env && curl -s -u "${SHUTTERSTOCK_API_KEY}:${SHUTTERSTOCK_API_SECRET}" \
  "https://api.shutterstock.com/v2/images/search?query=QUERY&page=PAGE&per_page=20&image_type=photo&sort=popular&view=full"
```

Parameters:
- `image_type=photo` — exclude vectors and illustrations
- `sort=popular` — most popular first
- `view=full` — includes keywords and categories in response
- `page=1` through `page=5`, `per_page=20`

From each response, extract using python3:
- `description` from `.data[].description`
- `preview_url` from `.data[].assets.preview_1000.url` (fallback to `.assets.preview.url`)
- `keywords` from `.data[].keywords` (array of strings)
- `categories` from `.data[].categories` (array of objects with `.name`)
- `image_type` from `.data[].image_type` (should all be "photo" but verify)

Deduplicate by description. Save as `places.json` — array of objects:
```json
[
  {
    "description": "...",
    "preview_url": "https://...",
    "keywords": ["keyword1", "keyword2"],
    "categories": ["Travel", "Landmarks"]
  }
]
```

## Stage 3: LLM filtering -> places_filtered.json

Read `places.json` and analyze each entry. Keep ONLY entries that refer to **specific, real, visitable geographic places** (landmarks, buildings, natural sites, cities, etc.).

Discard entries that are:
- Generic landscapes without identifiable location
- Studio shots, portraits, food photos
- Abstract or artistic images

For each kept entry, extract/infer the **place name** from description + keywords. Save as `places_filtered.json`:
```json
[
  {
    "place_name": "Bran Castle",
    "description": "...",
    "preview_url": "https://...",
    "keywords": [...],
    "categories": [...]
  }
]
```

Deduplicate by `place_name` (keep the entry with the best/most descriptive entry).

## Stage 4: Geocoding with Nominatim -> places_geo.json

For each place in `places_filtered.json`, geocode using Nominatim (free, no API key needed):

```
curl -s "https://nominatim.openstreetmap.org/search?q=PLACE_NAME&format=json&limit=1" \
  -H "User-Agent: shutterstock2kml/1.0"
```

**IMPORTANT**: Add 1-second delay between requests (Nominatim rate limit).

Extract `lat` and `lon` from the first result. Skip places that return no results.

Save as `places_geo.json`:
```json
[
  {
    "place_name": "Bran Castle",
    "lat": 45.515,
    "lng": 25.367,
    "description": "...",
    "preview_url": "https://...",
    "keywords": [...],
    "categories": [...]
  }
]
```

## Stage 5: Ratings via WebSearch -> places_rated.json

For each place in `places_geo.json`, use the `WebSearch` tool to search for:
`"PLACE_NAME" rating reviews`

Extract from search results:
- `rating` (float, e.g. 4.5) — Google Maps or TripAdvisor rating
- `review_count` (int) — number of reviews
- `google_maps_link` — format as `https://www.google.com/maps/search/PLACE_NAME` (URL-encoded)

If rating/reviews not found, set `rating: null`, `review_count: 0`.

Use Agent subagents to parallelize WebSearch calls (batch of 5-10 at a time).

Save as `places_rated.json`:
```json
[
  {
    "place_name": "Bran Castle",
    "lat": 45.515,
    "lng": 25.367,
    "rating": 4.5,
    "review_count": 12345,
    "google_maps_link": "https://www.google.com/maps/search/Bran+Castle",
    "description": "...",
    "preview_url": "https://...",
    "keywords": [...],
    "categories": [...]
  }
]
```

## Stage 6: Generate result.kml

Generate `result.kml` from `places_rated.json`.

### Icon color logic (visit-worthiness)

Based on rating AND review count, assign a color:
- **Green** (must visit): rating >= 4.5 AND review_count >= 1000
- **Yellow** (worth visiting): rating >= 4.0 AND review_count >= 100
- **Red** (skip or unknown): everything else (low rating, few reviews, or no data)

### Category detection

Determine category from keywords and categories arrays:
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

```xml
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Shutterstock Places: QUERY_REGION</name>

  <!-- All Style definitions here (3 colors x 6 categories = 18 styles) -->

  <!-- Placemarks grouped by category in Folders -->
  <Folder>
    <name>Castles</name>
    <!-- Placemarks with styleUrl="#COLOR-castle" -->
  </Folder>
  <Folder>
    <name>Religious Sites</name>
  </Folder>
  <Folder>
    <name>Nature</name>
  </Folder>
  <Folder>
    <name>Museums</name>
  </Folder>
  <Folder>
    <name>Urban</name>
  </Folder>
  <Folder>
    <name>Other</name>
  </Folder>
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
    <p>Rating: RATING/5 (REVIEW_COUNT reviews)</p>
    <p><img src="PREVIEW_URL" width="400" /></p>
    <p><em>DESCRIPTION</em></p>
  ]]></description>
</Placemark>
```

Sort placemarks within each folder by rating (highest first).

## Verification

After generating `result.kml`:
1. Validate XML: `python3 -c "import xml.etree.ElementTree as ET; ET.parse('result.kml'); print('Valid XML')"`
2. Report summary: total places, green/yellow/red counts, categories breakdown
