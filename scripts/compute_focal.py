#!/usr/bin/env python3
"""
Computes a smart-crop focal point for every post's featured image and writes
data/focal.json, which the theme uses as CSS object-position in post lists.

  {"<post folder name>": [x_percent, y_percent], ...}

Images stay remote (media.json). Each one is downloaded, analysed once with
smartcrop, and skipped on later runs, so new posts are cheap to add.

Setup:   pip install smartcrop pillow
Usage:   python scripts/compute_focal.py                  # all posts missing from focal.json
         python scripts/compute_focal.py --year 2026      # one year
         python scripts/compute_focal.py --year 2026 --month 10
         python scripts/compute_focal.py --force          # recompute everything
         python scripts/compute_focal.py --limit 20       # quick test
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import smartcrop
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
SITE_DIR = SCRIPT_DIR.parent
CONTENT_DIR = SITE_DIR / "content" / "news"
OUT_FILE = SITE_DIR / "data" / "focal.json"

# Card image box on desktop is 4:3. Percentages are computed for this ratio.
TARGET_W, TARGET_H = 4, 3
ANALYSE_WIDTH = 400  # downscale before analysis: far faster, same result
TIMEOUT = 30


def load_existing() -> dict:
    if OUT_FILE.exists():
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    return {}


def featured_urls(year: str | None, month: str | None) -> dict[str, str]:
    """{post folder name: featured image URL} for every post with a media.json."""
    found = {}
    pattern = f"{year or '*'}/{month or '*'}/*/media.json"
    for path in sorted(CONTENT_DIR.glob(pattern)):
        try:
            featured = json.loads(path.read_text(encoding="utf-8")).get("featured")
        except (OSError, json.JSONDecodeError):
            continue
        if featured:
            found[path.parent.name] = featured
    return found


def focal_point(url: str) -> list[int]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        img = Image.open(io.BytesIO(resp.read())).convert("RGB")

    orig_w, orig_h = img.size
    scale = ANALYSE_WIDTH / orig_w
    if scale < 1:
        img = img.resize((ANALYSE_WIDTH, max(1, round(orig_h * scale))))
    w, h = img.size

    # Largest TARGET_W:TARGET_H window that fits in the image.
    crop_w = min(w, h * TARGET_W / TARGET_H)
    crop_h = crop_w * TARGET_H / TARGET_W
    result = smartcrop.SmartCrop().crop(img, width=round(crop_w), height=round(crop_h))
    box = result["top_crop"]

    # CSS object-position: offset = pct * (overflow), so invert it.
    def pct(offset: float, overflow: float) -> int:
        return 50 if overflow <= 0 else max(0, min(100, round(offset / overflow * 100)))

    return [pct(box["x"], w - box["width"]), pct(box["y"], h - box["height"])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year")
    ap.add_argument("--month")
    ap.add_argument("--force", action="store_true", help="recompute entries that already exist")
    ap.add_argument("--limit", type=int, help="process at most N images")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    data = load_existing()
    todo = {k: u for k, u in featured_urls(args.year, args.month).items() if args.force or k not in data}
    if args.limit:
        todo = dict(list(todo.items())[: args.limit])
    print(f"{len(todo)} images to analyse ({len(data)} already stored)")

    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(focal_point, url): key for key, url in todo.items()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                data[key] = fut.result()
                done += 1
            except Exception as exc:  # network error, broken image, SVG, etc.
                failed += 1
                print(f"  skip {key}: {exc}", file=sys.stderr)
            if (done + failed) % 50 == 0:
                print(f"  {done + failed}/{len(todo)}")
                save(data)
    save(data)
    print(f"done: {done} analysed, {failed} skipped -> {OUT_FILE.relative_to(SITE_DIR)}")
    return 0


def save(data: dict) -> None:
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(dict(sorted(data.items())), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
