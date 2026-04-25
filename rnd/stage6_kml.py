#!/usr/bin/env python3
"""Stage 6: generate result.kml from places_rated.json.

Output rules (current spec):
- Drop entries with rating < 3.8 (entries with rating == None pass through).
- All placemarks share one circular paddle shape; review_count is encoded in
  the icon colour. We use *pre-coloured* icons rather than a <color> tint
  overlay because Google MyMaps (a common KML viewer) strips IconStyle/color
  on import and renders everything as a default white pin.
    review_count > 10000  -> darkgreen   (top-tier, scaled 1.4)
    review_count > 1000   -> green       (popular)
    review_count > 100    -> yellow      (mid-tier)
    review_count <= 100    -> orange      (niche)
    review_count is None   -> red         (unknown)
  Top tier ("darkgreen") reuses the green icon at a larger scale because the
  standard paddle palette only has one shade of green.
- Flat list — no folders. Sort by review_count desc, None last.
"""
import json, os, sys, html as html_lib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGION = "Romania"
RATING_MIN = 3.8

# Pre-coloured paddle icons. URL pattern is canonical Google Maps and MyMaps
# fetches them as-is (unlike <color> overlay, which MyMaps drops).
ICON_HREF = {
    "darkgreen": "http://maps.google.com/mapfiles/kml/paddle/grn-circle.png",
    "green":     "http://maps.google.com/mapfiles/kml/paddle/grn-circle.png",
    "yellow":    "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png",
    "orange":    "http://maps.google.com/mapfiles/kml/paddle/orange-circle.png",
    "red":       "http://maps.google.com/mapfiles/kml/paddle/red-circle.png",
}
SCALE = {"darkgreen": 1.4, "green": 1.0, "yellow": 1.0, "orange": 1.0, "red": 1.0}

def color_for(review_count):
    if review_count is None:
        return "red"
    if review_count > 10000:
        return "darkgreen"
    if review_count > 1000:
        return "green"
    if review_count > 100:
        return "yellow"
    return "orange"

def render_styles():
    out = []
    for color, href in ICON_HREF.items():
        out.append(f"""
  <Style id="{color}">
    <IconStyle>
      <scale>{SCALE[color]}</scale>
      <Icon><href>{href}</href></Icon>
    </IconStyle>
  </Style>""")
    return "\n".join(out)

def cdata_description(p):
    name = html_lib.escape(p["place_name"])
    link = p["google_maps_link"]
    rating = p.get("rating")
    count = p.get("review_count")
    desc = html_lib.escape(p.get("description", "") or "")
    preview = p.get("preview_url") or ""
    rating_str = "Rating: not found"
    if rating is not None:
        rating_str = f"Rating: {rating}/5"
        if count:
            rating_str += f" ({count:,} reviews)"
    return f"""<![CDATA[
    <h3><a href=\"{link}\">{name} on Google Maps</a></h3>
    <p>{rating_str}</p>
    <p><img src=\"{preview}\" width=\"400\" /></p>
    <p><em>{desc}</em></p>
  ]]>"""

def render_placemark(p):
    color = color_for(p.get("review_count"))
    return f"""
  <Placemark>
    <name>{html_lib.escape(p['place_name'])}</name>
    <styleUrl>#{color}</styleUrl>
    <Point><coordinates>{p['lng']},{p['lat']},0</coordinates></Point>
    <description>{cdata_description(p)}</description>
  </Placemark>"""

def main():
    src = json.load(open(os.path.join(ROOT, "places_rated.json")))
    kept = [p for p in src if p.get("rating") is None or p.get("rating") >= RATING_MIN]
    dropped = len(src) - len(kept)
    # Sort by review_count desc; None goes last.
    kept.sort(key=lambda p: (p.get("review_count") is None, -(p.get("review_count") or 0)))

    counts = {c: 0 for c in ICON_HREF}
    for p in kept:
        counts[color_for(p.get("review_count"))] += 1

    placemarks = "".join(render_placemark(p) for p in kept)
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Shutterstock Places: {REGION}</name>
{render_styles()}
{placemarks}
</Document>
</kml>
"""
    out = os.path.join(ROOT, "result.kml")
    with open(out, "w") as f:
        f.write(kml)
    print(f"[stage6] wrote {out}", file=sys.stderr)
    print(f"  kept: {len(kept)} (dropped by rating<{RATING_MIN}: {dropped})", file=sys.stderr)
    print(f"  by colour (review_count buckets):", file=sys.stderr)
    print(f"    darkgreen >10000:  {counts['darkgreen']}", file=sys.stderr)
    print(f"    green     >1000:   {counts['green']}", file=sys.stderr)
    print(f"    yellow    >100:    {counts['yellow']}", file=sys.stderr)
    print(f"    orange    <=100:   {counts['orange']}", file=sys.stderr)
    print(f"    red       unknown: {counts['red']}", file=sys.stderr)

if __name__ == "__main__":
    main()
