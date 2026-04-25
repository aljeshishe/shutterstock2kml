#!/usr/bin/env python3
"""Stage 2: scrape Shutterstock search results via patchright (Chrome) -> places.json.

Why not the official API any more:
- API requires paid keys; we now have a working bypass for DataDome.
- The site behind DataDome is fine when driven by `patchright` (a Playwright
  fork that hides automation leaks) + system Chrome + a persistent profile in
  headful mode. A warm-up homepage hit lets DataDome issue cookies before we
  load the search URL.

Per-page flow:
  1. visit homepage to warm up cookies (only on the first page in the run)
  2. navigate to /search/<q>?image_type=photo&sort=popular&page=N
  3. wait for `main img[alt]` to appear, then scroll to trigger lazy load
  4. extract { description (alt text), preview_url (img src) } per photo card

We keep the same browser context across pages and queries — re-warming each
time is wasteful and looks more bot-like.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

from patchright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / ".browser-profile"
PAGES_PER_QUERY = int(os.environ.get("STAGE2_PAGES", "5"))

EXTRACT_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    document.querySelectorAll('main img[alt]').forEach((img) => {
        const alt = (img.alt || '').trim();
        if (alt.length < 4) return;
        const src = img.currentSrc || img.src || '';
        if (!src || src.startsWith('data:')) return;
        if (seen.has(alt)) return;
        seen.add(alt);
        out.push({ description: alt, preview_url: src });
    });
    return out;
}
"""


def warmup(page) -> None:
    page.goto("https://www.shutterstock.com/", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2500)
    page.mouse.move(200, 200)
    page.mouse.move(500, 400, steps=12)
    page.wait_for_timeout(600)


def scrape_page(page, url: str) -> list[dict]:
    print(f"[stage2] {url}", file=sys.stderr)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    for _ in range(25):
        ready = page.evaluate(
            "() => !!document.querySelector('main img[alt], a[href*=\"/image-photo/\"] img[alt]')"
        )
        if ready:
            break
        page.wait_for_timeout(2000)

    for _ in range(12):
        page.evaluate("() => window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(500)

    return page.evaluate(EXTRACT_JS) or []


def main() -> int:
    queries = json.loads((ROOT / "queries.json").read_text())
    PROFILE_DIR.mkdir(exist_ok=True)

    headful = os.environ.get("HEADLESS") != "1"
    print(f"[stage2] mode={'headful' if headful else 'headless'}, pages_per_query={PAGES_PER_QUERY}", file=sys.stderr)

    seen: set[str] = set()
    places: list[dict] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not headful,
            channel="chrome",
            no_viewport=True,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        warmup(page)

        for q in queries:
            for page_n in range(1, PAGES_PER_QUERY + 1):
                # `?page=1` gets normalised to the bare URL and can ERR_ABORT;
                # use the bare path for the first page.
                base = f"https://www.shutterstock.com/search/{quote(q)}?image_type=photo"
                url = base if page_n == 1 else f"{base}&page={page_n}"
                try:
                    items = scrape_page(page, url)
                except Exception as e:
                    print(f"  error: {type(e).__name__}: {e}", file=sys.stderr)
                    page.wait_for_timeout(1500)
                    try:
                        items = scrape_page(page, url)
                    except Exception as e2:
                        print(f"  retry failed: {type(e2).__name__}: {e2}", file=sys.stderr)
                        continue
                added = 0
                for it in items:
                    desc = it["description"]
                    if desc in seen:
                        continue
                    seen.add(desc)
                    places.append({
                        "description": desc,
                        "preview_url": it["preview_url"],
                        "keywords": [],
                        "categories": [],
                    })
                    added += 1
                print(f"  page {page_n}: {len(items)} cards, +{added} new (total {len(places)})", file=sys.stderr)
                if not items:
                    break

        context.close()

    out = ROOT / "places.json"
    out.write_text(json.dumps(places, ensure_ascii=False, indent=2))
    print(f"[stage2] wrote {len(places)} places -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
