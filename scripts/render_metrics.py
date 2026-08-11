#!/usr/bin/env python3
"""Measure rendered page images for blank pages and edge overflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import now_iso, save_json, sha256_file

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError as exc:
    raise SystemExit("Pillow is required for render metrics.") from exc


def metrics(path: Path, edge_ratio: float) -> dict:
    with Image.open(path) as original:
        image = original.convert("L")
        width, height = image.size
        pixels = width * height
        histogram = image.histogram()
        nonwhite = sum(histogram[:248])
        dark = sum(histogram[:220])
        edge_x = max(1, int(width * edge_ratio))
        edge_y = max(1, int(height * edge_ratio))
        edge_mask = Image.new("L", image.size, 255)
        center = Image.new("L", (width - 2 * edge_x, height - 2 * edge_y), 0)
        edge_mask.paste(center, (edge_x, edge_y))
        ink = image.point(lambda value: 0 if value < 248 else 255)
        edge_ink = ImageChops.darker(ink, edge_mask)
        edge_nonwhite = sum(1 for value in edge_ink.getdata() if value < 248)
        return {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "width": width,
            "height": height,
            "nonwhite_ratio": round(nonwhite / pixels, 6),
            "dark_ratio": round(dark / pixels, 6),
            "edge_ink_ratio": round(edge_nonwhite / pixels, 6),
            "blank": nonwhite / pixels < 0.0015,
            "possible_edge_overflow": edge_nonwhite / pixels > 0.002
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--edge-ratio", type=float, default=0.015)
    args = parser.parse_args()
    images = sorted([p for p in args.images.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    if not images:
        parser.error(f"No rendered page images in {args.images}")
    pages = [metrics(path, args.edge_ratio) for path in images]
    result = {
        "generated_at": now_iso(),
        "page_count": len(pages),
        "blank_pages": [index + 1 for index, page in enumerate(pages) if page["blank"]],
        "possible_edge_overflow_pages": [index + 1 for index, page in enumerate(pages) if page["possible_edge_overflow"]],
        "pages": pages
    }
    save_json(args.output.resolve(), result)
    print(f"RENDER_METRICS={args.output.resolve()}")
    print(f"PAGES={len(pages)} BLANK={len(result['blank_pages'])} EDGE={len(result['possible_edge_overflow_pages'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
