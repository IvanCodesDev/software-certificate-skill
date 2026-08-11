#!/usr/bin/env python3
"""Import, order, deduplicate, and quality-check user-supplied screenshots."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from capture_web_screenshots import hamming_hex, image_metrics, quality_findings
from common import load_json, now_iso, save_json, sha256_file

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--report", required=True, type=Path)
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
    for index, path in enumerate(files, 1):
        digest = sha256_file(path)
        metrics = image_metrics(path)
        duplicate_of = hashes.get(digest)
        if duplicate_of is None:
            for prior_id, prior_hash, width, height in dhashes:
                if (width, height) == (metrics["width"], metrics["height"]) and hamming_hex(prior_hash, metrics["dhash"]) <= 1:
                    duplicate_of = prior_id
                    break
        planned_item = planned[index - 1] if index <= len(planned) else {}
        shot_id = planned_item.get("id") or f"user-shot-{index:03d}"
        findings = quality_findings(metrics, {
            "min_width": 900, "min_height": 500, "min_entropy": 0.8,
            "min_content_ratio": 0.002, "max_near_white_ratio": 0.997,
        })
        if duplicate_of:
            findings.append({"code": "near_duplicate", "duplicate_of": duplicate_of})
        title = title_by_index[index - 1] if index <= len(title_by_index) else path.stem
        target = output / f"{index:03d}-{shot_id}{path.suffix.lower()}"
        if not duplicate_of:
            shutil.copy2(path, target)
            hashes[digest] = shot_id
            dhashes.append((shot_id, metrics["dhash"], metrics["width"], metrics["height"]))
        captures.append({
            "id": shot_id, "title": title, "status": "pass" if not findings else "quality_warning",
            "source_path": str(path), "path": str(target) if not duplicate_of else None,
            "sha256": digest, "role": role_by_index[index - 1] if index <= len(role_by_index) else None,
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
            "duplicates": sum(any(f.get("code") == "near_duplicate" for f in item["quality_findings"]) for item in captures),
            "missing_planned": missing_planned, "unplanned_files": unplanned_files,
            "errors": 0 if state == "captured" else 1,
        }
    }
    save_json(args.report.resolve(), report)
    print(f"SCREENSHOT_INDEX={args.report.resolve()}")
    print(f"PROVIDED={len(files)} PASSED={report['summary']['passed']} WARNINGS={report['summary']['quality_warnings']} MISSING={report['summary']['missing_planned']}")
    return 0 if state == "captured" else 3


if __name__ == "__main__":
    raise SystemExit(main())
