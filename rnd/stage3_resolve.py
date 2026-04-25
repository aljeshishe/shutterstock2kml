#!/usr/bin/env python3
"""Stage 3: resolve Shutterstock entries to real Google Maps places via Playwright.

In one shot per entry, we get:
  - place_name (canonical Google Maps name)
  - lat / lng (from URL after redirect)
  - rating, review_count

Entries that don't resolve to a place card are dropped (Google Maps couldn't
identify a real location). Final list is deduplicated by place_name.
"""
import asyncio, json, os, re, sys, time, urllib.parse
from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

EXTRACT_JS = r"""() => {
  const root = document.querySelector('[role="main"]') || document.body;
  // Single place card (Google Maps resolved the query directly).
  const titleEl = root.querySelector('h1.DUwDvf, h1[class*="DUwDvf"]');
  const node    = root.querySelector('div.F7nice, div[class*="F7nice"]');
  if (titleEl && node) {
    const ratingEl = node.querySelector('span[aria-hidden="true"]');
    const countEl  = node.querySelector('span[aria-label*="review"]');
    return {
      kind: 'card',
      place_name:  titleEl.innerText.trim(),
      rating_text: ratingEl ? ratingEl.innerText.trim() : null,
      count_label: countEl  ? countEl.getAttribute('aria-label') : null,
    };
  }
  // Results list — Google Maps showed multiple candidates instead of a card.
  const feed = root.querySelector('div[role="feed"]');
  if (feed) {
    const items = [];
    for (const a of feed.querySelectorAll('a[href*="/maps/place/"]')) {
      const name = (a.getAttribute('aria-label') || '').trim();
      if (!name) continue;
      const card = a.closest('div.Nv2PK, div[jsaction*="mouseover"]') || a.parentElement;
      let rating = null, count = null;
      if (card) {
        const rEl = card.querySelector('span[role="img"][aria-label*="stars"], span.MW4etd');
        if (rEl) {
          const t = rEl.getAttribute('aria-label') || rEl.innerText || '';
          const m = t.match(/(\d+[.,]\d+)/);
          if (m) rating = parseFloat(m[1].replace(',', '.'));
        }
        const cEl = card.querySelector('span.UY7F9, span[aria-label*="review"]');
        if (cEl) {
          const t = cEl.getAttribute('aria-label') || cEl.innerText || '';
          const digits = t.replace(/[^0-9]/g, '');
          if (digits) count = parseInt(digits, 10);
        }
      }
      items.push({ name, href: a.getAttribute('href'), rating, count });
    }
    return { kind: 'feed', items };
  }
  return null;
}"""

LATLNG_AT_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
LATLNG_3D4D_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")

# Feed-result quality bar (per user spec): keep only places with rating > 3.8
# AND review_count > 100. Items missing either are skipped.
FEED_MIN_RATING = 3.8
FEED_MIN_COUNT  = 100


def extract_latlng(url: str):
    m = LATLNG_3D4D_RE.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = LATLNG_AT_RE.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


async def _dismiss_consent(page):
    for sel in ('button:has-text("Accept all")',
                'button:has-text("Reject all")',
                'form[action*="consent"] button'):
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("domcontentloaded")
                return
        except Exception:
            pass


async def _wait_for_coords(page, timeout_s=5.0):
    end = time.time() + timeout_s
    while time.time() < end:
        lat, lng = extract_latlng(page.url)
        if lat is not None:
            return lat, lng
        await asyncio.sleep(0.3)
    return None, None


async def _resolve_feed_item(page, item):
    """Navigate to a feed item's href to obtain canonical name + coords. Rating
    and review_count are taken from the feed listing (already filtered by
    quality)."""
    href = item.get("href")
    if not href:
        return None
    url = href if href.startswith("http") else f"https://www.google.com{href}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return None
    end = time.time() + 8.0
    canonical = None
    while time.time() < end:
        try:
            info = await page.evaluate(EXTRACT_JS)
        except Exception:
            info = None
        if info and info.get("kind") == "card" and info.get("place_name"):
            canonical = info["place_name"]
            break
        await asyncio.sleep(0.3)
    lat, lng = await _wait_for_coords(page, 4.0)
    if lat is None or lng is None:
        return None
    return {
        "place_name": canonical or item["name"],
        "lat": lat,
        "lng": lng,
        "rating": item.get("rating"),
        "review_count": item.get("count"),
    }


async def resolve_one(page, query, timeout_s=10.0):
    """Return a list of resolved places. Direct card → 1-element list. Results
    list → all entries passing rating>3.8 & count>100, each with coords. Empty
    list when nothing usable was rendered."""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(query)}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await _dismiss_consent(page)

    end = time.time() + timeout_s
    info = None
    while time.time() < end:
        try:
            info = await page.evaluate(EXTRACT_JS)
        except Exception:
            info = None
        if info and (info.get("kind") == "card" or (info.get("kind") == "feed" and info.get("items"))):
            break
        await asyncio.sleep(0.4)

    if not info:
        return []

    if info.get("kind") == "card":
        rating = None
        if info.get("rating_text"):
            try:
                rating = float(info["rating_text"])
            except Exception:
                rating = None
        count = None
        digits = "".join(ch for ch in (info.get("count_label") or "") if ch.isdigit())
        if digits:
            count = int(digits)
        lat, lng = await _wait_for_coords(page, 5.0)
        return [{
            "place_name": info["place_name"],
            "lat": lat,
            "lng": lng,
            "rating": rating,
            "review_count": count,
        }]

    # Feed branch: filter by quality bar, then keep the single most-reviewed
    # candidate. Prior behaviour returned all matches, but that flooded the
    # output with peripheral places (restaurants near a food query, etc.). The
    # top-reviewed one is almost always the intended landmark.
    kept = [
        it for it in (info.get("items") or [])
        if (it.get("rating") or 0) > FEED_MIN_RATING and (it.get("count") or 0) > FEED_MIN_COUNT
    ]
    if not kept:
        return []
    best = max(kept, key=lambda it: it.get("count") or 0)
    resolved = await _resolve_feed_item(page, best)
    if resolved and resolved["lat"] is not None:
        return [resolved]
    return []


async def worker(name, browser, queue, results, region):
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-US")
    page = await ctx.new_page()
    while True:
        try:
            entry = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        desc = entry["description"]
        query = desc if region.lower() in desc.lower() else f"{desc} {region}"
        try:
            resolved_list = await resolve_one(page, query)
        except Exception as e:
            print(f"  [{name}] DROP: {desc[:160]!r} -> ERR {type(e).__name__}", file=sys.stderr)
            resolved_list = []
        if not resolved_list:
            print(f"  [{name}] DROP: {desc[:160]!r}", file=sys.stderr)
            continue
        kept_in_query = 0
        for resolved in resolved_list:
            if not resolved.get("place_name"):
                continue
            if resolved["lat"] is None or resolved["lng"] is None:
                print(f"  [{name}] DROP (no coords): {resolved['place_name']!r}", file=sys.stderr)
                continue
            link = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(resolved['place_name'])}"
            merged = {
                **resolved,
                "google_maps_link": link,
                "description": entry.get("description"),
                "preview_url": entry.get("preview_url"),
                "keywords": entry.get("keywords") or [],
                "categories": entry.get("categories") or [],
            }
            tag = "OK" if len(resolved_list) == 1 else "OK[feed]"
            print(f"  [{name}] {tag}: {merged['place_name']!r} rating={merged['rating']} count={merged['review_count']}", file=sys.stderr)
            results.append(merged)
            kept_in_query += 1
        if kept_in_query == 0:
            print(f"  [{name}] DROP (feed all-filtered): {desc[:160]!r}", file=sys.stderr)
    await ctx.close()


async def run():
    region = os.environ.get("STAGE3_REGION") or json.load(open(os.path.join(ROOT, "queries.json")))[0]
    workers = int(os.environ.get("STAGE3_WORKERS", "12"))
    # Prefer the post-filter file if present; fall back to raw places.json.
    filtered_path = os.path.join(ROOT, "places_filtered.json")
    raw_path = os.path.join(ROOT, "places.json")
    src_path = filtered_path if os.path.exists(filtered_path) else raw_path
    src = json.load(open(src_path))
    print(f"[stage3] resolving {len(src)} entries in region={region!r} with {workers} workers", file=sys.stderr)

    queue = asyncio.Queue()
    for e in src:
        queue.put_nowait(e)
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        await asyncio.gather(*(worker(f"w{i}", browser, queue, results, region) for i in range(workers)))
        await browser.close()

    # dedup by place_name; keep the one with the most informative description
    by_name = {}
    for r in results:
        name = r["place_name"]
        prev = by_name.get(name)
        if prev is None or len(r.get("description") or "") > len(prev.get("description") or ""):
            by_name[name] = r
    final = list(by_name.values())
    # stable order: by rating desc, None last
    final.sort(key=lambda p: (p.get("rating") is None, -(p.get("rating") or 0)))

    out = os.path.join(ROOT, "places_rated.json")
    with open(out, "w") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    rated = sum(1 for r in final if r.get("rating") is not None)
    print(f"[stage3] resolved {len(final)} unique places ({rated} with rating) -> {out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(run())
