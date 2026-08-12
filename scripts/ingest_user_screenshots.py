#!/usr/bin/env python3
"""Import, crop, order, deduplicate, and quality-check user screenshots."""

from __future__ import annotations

import argparse
import shutil
import statistics
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from capture_web_screenshots import hamming_hex, image_metrics, quality_findings
from common import load_json, now_iso, save_json, sha256_file

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ANALYSIS_WIDTH = 512


def _rows(gray_values: list[int], width: int, y: int) -> list[int]:
    return gray_values[y * width:(y + 1) * width]


def _content_bbox(img: Image.Image, background, tolerance: int) -> tuple[int, int, int, int] | None:
    difference = ImageChops.difference(img, Image.new("RGB", img.size, background)).convert("L")
    return difference.point(lambda v, t=tolerance: 255 if v > t else 0).getbbox()


def _color_points(pixels, width: int, predicate) -> list[tuple[int, int]]:
    return [(i % width, i // width) for i, c in enumerate(pixels) if predicate(c)]


def _row_bands(points: list[tuple[int, int]]) -> list[tuple[float, float]]:
    """Group same-coloured pixels into row bands, returning (x, y) centres."""
    bands: dict[int, list[tuple[int, int]]] = {}
    for x, y in points:
        bands.setdefault(round(y / 3), []).append((x, y))
    centres = []
    for group in bands.values():
        if len(group) >= 3:
            centres.append((statistics.fmean(p[0] for p in group),
                            statistics.fmean(p[1] for p in group)))
    return centres


def _find_traffic_dots(pixels, width: int, window_width: int) -> tuple[float, float, float] | None:
    """Return (x, y, pitch) of macOS traffic-light dots, or None.

    Page artwork can share the dot colours (red delete links, status dots),
    so every colour contributes candidate row bands and we look for the
    topmost aligned red-yellow-green triple with a plausible spacing.
    """
    red = _row_bands(_color_points(pixels, width, lambda c: c[0] > 195 and c[1] < 130 and c[2] < 125))
    yellow = _row_bands(_color_points(pixels, width, lambda c: c[0] > 210 and c[1] > 150 and c[2] < 110))
    green = _row_bands(_color_points(pixels, width, lambda c: c[0] < 130 and c[1] > 150 and c[2] < 130))
    best = None
    for red_x, red_y in red:
        for yellow_x, yellow_y in yellow:
            if abs(yellow_y - red_y) > 2:
                continue
            for green_x, green_y in green:
                if abs(green_y - yellow_y) > 2:
                    continue
                pitch = (green_x - red_x) / 2
                if not (red_x < yellow_x < green_x):
                    continue
                if not (window_width * 0.008 <= pitch <= window_width * 0.06):
                    continue
                if best is None or red_y < best[1]:
                    best = (red_x, red_y, pitch)
    return best


def detect_browser_chrome(image: Image.Image) -> tuple[tuple[int, int, int, int], dict[str, Any]] | None:
    """Locate the web-page area inside a browser-window screenshot.

    Filing manuals should show the page, not the browser. The detector only
    crops when it is confident: macOS traffic-light dots (or an address-bar
    pill) anchored in the window's top strip. Desktop apps and plain page
    captures are left untouched.
    """
    rgb = image.convert("RGB")
    scale = ANALYSIS_WIDTH / rgb.width
    small = rgb.resize((ANALYSIS_WIDTH, max(24, round(rgb.height * scale))))
    offset_x, offset_y = 0, 0
    # Marketing shots pad the window with transparency or a flat backdrop:
    # peel those layers before looking for the window itself.
    if "A" in image.getbands():
        alpha = image.getchannel("A").resize(small.size)
        solid = alpha.point(lambda v: 255 if v > 16 else 0).getbbox()
        if solid:
            offset_x, offset_y = solid[0], solid[1]
            small = small.crop(solid)
    backdrop = small.getpixel((2, 2))
    inner = _content_bbox(small, backdrop, 18)
    if inner and (inner[2] - inner[0]) >= small.width * 0.5:
        offset_x += inner[0]
        offset_y += inner[1]
        small = small.crop(inner)
    win_w, win_h = small.size
    if win_w < ANALYSIS_WIDTH * 0.4 or win_h < 40:
        return None
    strip_h = max(8, int(win_h * 0.14))
    strip_w = max(24, int(win_w * 0.30))
    pixels = list(small.crop((0, 0, strip_w, strip_h)).getdata())
    dots = _find_traffic_dots(pixels, strip_w, win_w)

    boundary = None
    style = ""
    limit = max(10, int(win_h * 0.2))
    gray = list(small.crop((0, 0, win_w, min(win_h, limit + 3))).convert("L").getdata())

    def edge_strength(y: int) -> float:
        above, below = _rows(gray, win_w, y), _rows(gray, win_w, y + 1)
        return sum(abs(above[i] - below[i]) for i in range(0, win_w, 2)) / (win_w / 2)

    if dots:
        red_x, red_y, pitch = dots
        # Standard macOS chrome: content starts ~3.2 dot pitches below the
        # dot row (tab strip + address bar). Snap to a nearby row edge.
        estimate = int(round(red_y + 3.2 * pitch))
        window = max(2, int(round(pitch)))
        candidates = [y for y in range(max(2, estimate - window), min(limit, estimate + window + 1))
                      if edge_strength(y) > 4]
        # Snap to the edge nearest the geometric estimate: in-page header
        # borders can also fall inside the search window and must not win.
        boundary = min(candidates, key=lambda y: (abs(y - estimate), y)) if candidates else min(estimate, limit)
        style = "mac_frame"
    if boundary is None:
        # Windows/Chrome style: an address-bar pill row (flat side margins in
        # one tone, distinct flat field in the centre) inside the top strip.
        pill_bottom = None
        for y in range(2, limit):
            row = _rows(gray, win_w, y)
            side = row[: max(4, int(win_w * 0.05))]
            middle = row[int(win_w * 0.30):int(win_w * 0.60)]
            if statistics.pstdev(side) < 6 and statistics.pstdev(middle) < 6 \
                    and abs(statistics.fmean(side) - statistics.fmean(middle)) > 12:
                pill_bottom = y
        if pill_bottom is None:
            return None
        below = [y for y in range(pill_bottom + 1, limit) if edge_strength(y) > 5]
        if not below:
            return None
        boundary = below[0]
        style = "browser_window"
    inv = 1 / scale
    inset = max(2, int(0.004 * rgb.width))
    box = (int(offset_x * inv) + inset, int((offset_y + boundary + 1) * inv) + 1,
           int((offset_x + win_w) * inv) - inset, int((offset_y + win_h) * inv) - inset)
    box = (max(0, box[0]), max(0, box[1]), min(rgb.width, box[2]), min(rgb.height, box[3]))
    if box[2] - box[0] < rgb.width * 0.4 or box[3] - box[1] < rgb.height * 0.35:
        return None
    return box, {"style": style, "box": list(box), "original_size": [rgb.width, rgb.height]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--crop-browser-chrome", choices=["auto", "off"], default="auto")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = load_json(args.plan) if args.plan and args.plan.exists() else {"captures": []}
    planned = plan.get("captures", [])
    title_by_index = [item.get("title", item.get("id", "截图")) for item in planned]
    evidence_by_index = [item.get("evidence_ids", []) for item in planned]
    role_by_index = [item.get("role") for item in planned]
    files = sorted((path for path in source.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
                   key=lambda path: path.name.lower())
    captures: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    dhashes: list[tuple[str, str, int, int]] = []
    cropped_count = 0
    for index, path in enumerate(files, 1):
        source_digest = sha256_file(path)
        planned_item = planned[index - 1] if index <= len(planned) else {}
        shot_id = planned_item.get("id") or f"user-shot-{index:03d}"
        duplicate_of = hashes.get(source_digest)
        target = output / f"{index:03d}-{shot_id}{path.suffix.lower()}"
        crop_meta: dict[str, Any] | None = None
        if duplicate_of is None:
            if args.crop_browser_chrome == "auto":
                with Image.open(path) as image:
                    image.load()
                    detected = detect_browser_chrome(image)
                    if detected:
                        box, crop_meta = detected
                        image.convert("RGB").crop(box).save(target)
                        cropped_count += 1
            if crop_meta is None:
                shutil.copy2(path, target)
        digest = sha256_file(target) if duplicate_of is None else source_digest
        metrics = image_metrics(target) if duplicate_of is None else image_metrics(path)
        if duplicate_of is None:
            for prior_id, prior_hash, width, height in dhashes:
                if (width, height) == (metrics["width"], metrics["height"]) and hamming_hex(prior_hash, metrics["dhash"]) <= 1:
                    duplicate_of = prior_id
                    target.unlink(missing_ok=True)
                    break
        findings = quality_findings(metrics, {
            # Sparse admin pages are legitimately low-entropy once the browser
            # chrome is cropped away; blankness is still caught by the
            # content-ratio and near-white checks.
            "min_width": 900, "min_height": 500, "min_entropy": 0.5,
            "min_content_ratio": 0.002, "max_near_white_ratio": 0.997,
        })
        if duplicate_of:
            findings.append({"code": "near_duplicate", "duplicate_of": duplicate_of})
        title = title_by_index[index - 1] if index <= len(title_by_index) else path.stem
        if not duplicate_of:
            hashes[source_digest] = shot_id
            dhashes.append((shot_id, metrics["dhash"], metrics["width"], metrics["height"]))
        captures.append({
            "id": shot_id, "title": title, "status": "pass" if not findings else "quality_warning",
            "source_path": str(path), "path": str(target) if not duplicate_of else None,
            "sha256": digest if not duplicate_of else source_digest,
            "source_sha256": source_digest,
            "browser_chrome_crop": crop_meta,
            "role": role_by_index[index - 1] if index <= len(role_by_index) else None,
            "url": "user-supplied", "evidence_ids": evidence_by_index[index - 1] if index <= len(evidence_by_index) else [],
            "captured_at": None, "metrics": metrics, "quality_findings": findings,
        })
    missing_planned = max(0, len(planned) - len(files))
    passed = sum(item["status"] == "pass" for item in captures)
    warnings = sum(item["status"] == "quality_warning" for item in captures)
    unplanned_files = max(0, len(files) - len(planned))
    state = "captured" if planned and len(files) == len(planned) and passed == len(planned) \
        and not warnings and not missing_planned else "failed"
    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "mode": "user_supplied",
        "state": state, "source": str(source), "output": str(output), "captures": captures,
        "summary": {
            "requested": len(planned), "provided": len(files),
            "passed": passed, "quality_warnings": warnings,
            "browser_chrome_cropped": cropped_count,
            "duplicates": sum(any(f.get("code") == "near_duplicate" for f in item["quality_findings"]) for item in captures),
            "missing_planned": missing_planned, "unplanned_files": unplanned_files,
            "errors": 0 if state == "captured" else 1,
        }
    }
    save_json(args.report.resolve(), report)
    print(f"SCREENSHOT_INDEX={args.report.resolve()}")
    print(f"PROVIDED={len(files)} PASSED={report['summary']['passed']} WARNINGS={report['summary']['quality_warnings']} "
          f"CROPPED={cropped_count} MISSING={report['summary']['missing_planned']}")
    return 0 if state == "captured" else 3


if __name__ == "__main__":
    raise SystemExit(main())
