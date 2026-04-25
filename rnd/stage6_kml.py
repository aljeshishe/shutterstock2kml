#!/usr/bin/env python3
"""Stage 6: generate result.kml from places_rated.json.

Uses the Google MyMaps icon style — `blank_maps.png` paddle pin with a `<color>`
overlay applied per bucket. 10 review_count buckets, log-spaced between 10 and
500_000, mapping a rainbow gradient red → orange → yellow → green → blue → violet.

Bucket thresholds (`review_count > T`):
   bucket 0:  <= 30           dark red       #B71C1C
   bucket 1:  30..100         red            #E53935
   bucket 2:  100..300        orange-red     #F4511E
   bucket 3:  300..1000       orange         #FB8C00
   bucket 4:  1000..3000      yellow         #FDD835
   bucket 5:  3000..10000     lime           #C0CA33
   bucket 6:  10000..30000    green          #7CB342
   bucket 7:  30000..100000   deep green     #43A047
   bucket 8:  100000..300000  blue           #1976D2
   bucket 9:  > 300000        violet         #8E24AA

Entries with rating < 3.8 are dropped (None passes through).
review_count == None falls into bucket 0.
"""
import json, os, sys, html as html_lib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGION = "Romania"
RATING_MIN = 3.8

# Gradient: low review_count -> light green; high review_count -> dark red.
# (id_suffix, KML aaBBGGRR colour, hex for label)
BUCKETS = [
    ("b0", "ffA0E4B7", "#B7E4A0"),  # very light green
    ("b1", "ff42B37C", "#7CB342"),  # light green
    ("b2", "ff50AF4C", "#4CAF50"),  # green
    ("b3", "ff33CAC0", "#C0CA33"),  # yellow-green / lime
    ("b4", "ff35D8FD", "#FDD835"),  # yellow
    ("b5", "ff008CFB", "#FB8C00"),  # orange
    ("b6", "ff1E51F4", "#F4511E"),  # orange-red
    ("b7", "ff3539E5", "#E53935"),  # red
    ("b8", "ff1C1CB7", "#B71C1C"),  # dark red
    ("b9", "ff00007F", "#7F0000"),  # very dark red
]

# Upper bound (inclusive) for buckets 0..8; bucket 9 catches the rest.
THRESHOLDS = [30, 100, 300, 1000, 3000, 10000, 30000, 100000, 300000]

ICON_HREF = "http://maps.google.com/mapfiles/kml/paddle/wht-blank.png"


def bucket_for(review_count):
    if review_count is None:
        return 0
    for i, t in enumerate(THRESHOLDS):
        if review_count <= t:
            return i
    return 9


def render_styles():
    out = []
    for _suffix, color, hexc in BUCKETS:
        rgb = hexc.lstrip("#")  # e.g. "B71C1C"
        sid = f"icon-1899-{rgb}"
        for variant, label_scale in (("normal", 0), ("highlight", 1)):
            out.append(f"""
    <Style id="{sid}-{variant}">
      <IconStyle>
        <color>{color}</color>
        <colorMode>normal</colorMode>
        <scale>1.1</scale>
        <Icon>
          <href>{ICON_HREF}</href>
        </Icon>
        <hotSpot x="32" xunits="pixels" y="1" yunits="fraction"/>
      </IconStyle>
      <LabelStyle>
        <scale>{label_scale}</scale>
      </LabelStyle>
    </Style>""")
        out.append(f"""
    <StyleMap id="{sid}">
      <Pair><key>normal</key><styleUrl>#{sid}-normal</styleUrl></Pair>
      <Pair><key>highlight</key><styleUrl>#{sid}-highlight</styleUrl></Pair>
    </StyleMap>""")
    return "".join(out)


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
    idx = bucket_for(p.get("review_count"))
    rgb = BUCKETS[idx][2].lstrip("#")
    return f"""
    <Placemark>
      <name>{html_lib.escape(p['place_name'])}</name>
      <styleUrl>#icon-1899-{rgb}</styleUrl>
      <Point><coordinates>{p['lng']},{p['lat']},0</coordinates></Point>
      <description>{cdata_description(p)}</description>
    </Placemark>"""


def main():
    src = json.load(open(os.path.join(ROOT, "places_rated.json")))
    kept = [p for p in src if p.get("rating") is None or p.get("rating") >= RATING_MIN]
    dropped = len(src) - len(kept)
    # Sort by review_count desc; None goes last.
    kept.sort(key=lambda p: (p.get("review_count") is None, -(p.get("review_count") or 0)))

    counts = [0] * len(BUCKETS)
    for p in kept:
        counts[bucket_for(p.get("review_count"))] += 1

    placemarks = "".join(render_placemark(p) for p in kept)
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Shutterstock Places: {REGION}</name>{render_styles()}{placemarks}
  </Document>
</kml>
"""
    out = os.path.join(ROOT, "result.kml")
    with open(out, "w") as f:
        f.write(kml)
    print(f"[stage6] wrote {out}", file=sys.stderr)
    print(f"  kept: {len(kept)} (dropped by rating<{RATING_MIN}: {dropped})", file=sys.stderr)
    labels = ["≤30","30–100","100–300","300–1k","1k–3k","3k–10k","10k–30k","30k–100k","100k–300k",">300k"]
    print(f"  by bucket (review_count, colour):", file=sys.stderr)
    for i, ((suffix, _kml, hexc), n, lbl) in enumerate(zip(BUCKETS, counts, labels)):
        print(f"    {i} {lbl:<10s} {hexc}: {n}", file=sys.stderr)

if __name__ == "__main__":
    main()
