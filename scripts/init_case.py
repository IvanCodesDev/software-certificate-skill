#!/usr/bin/env python3
"""Create an evidence-led filing workspace without overwriting confirmed facts."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import now_iso, save_json, sha256_file


DIRS = [
    "00-original",
    "01-intake",
    "01-rule-snapshot",
    "02-evidence/screenshots",
    "03-storyboard",
    "04-content",
    "05-source",
    "06-output/source",
    "07-qa/rendered",
    "08-release",
]


def write_if_absent(path: Path, value: dict) -> bool:
    if path.exists():
        return False
    save_json(path, value)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--preserve", action="append", default=[], type=Path,
                        help="Existing source material to hash and copy into 00-original")
    args = parser.parse_args()

    project = args.project.resolve()
    case = args.case.resolve()
    if not project.is_dir():
        parser.error(f"Project directory not found: {project}")
    for item in DIRS:
        (case / item).mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    facts = {
        "software_full_name": "【待申请人确认：软件全称】",
        "software_short_name": "【待申请人确认：软件简称】",
        "version": "V1.0",
        "rightsholder": "【待申请人确认：著作权人】",
        "completion_date": "【待申请人确认：开发完成日期】",
        "publication_status": "pending_confirmation",
        "development_mode": "independent",
        "rights_scope": "all",
        "project_root": str(project),
        "confirmed_by": "",
        "confirmed_at": ""
    }
    facts_path = case / "01-intake/application-facts.json"
    if write_if_absent(facts_path, facts):
        created.append(str(facts_path))

    attestation = {
        "checked_at": "",
        "checked_by": "",
        "current_form_url": "",
        "declaration_text_sha256": "",
        "independent_development_confirmed": False,
        "ai_use_statement_present": "pending_confirmation",
        "actual_tool_use_recorded": False,
        "applicant_page_by_page_review": False,
        "signature_ready": False,
        "notes": []
    }
    path = case / "01-intake/authorship-attestation.json"
    if write_if_absent(path, attestation):
        created.append(str(path))

    snapshot = {
        "as_of": "2026-08-11",
        "created_at": now_iso(),
        "jurisdiction": "CN",
        "sources": [
            {
                "level": "A",
                "title": "计算机软件著作权登记办法",
                "url": "https://www.ncac.gov.cn/xxfb/flfg/bmgz/202410/P020241015604759788122.pdf"
            },
            {
                "level": "B",
                "title": "2026 软件著作权登记流程实施参考",
                "url": "https://zcgs.nwpu.edu.cn/info/1048/22841.htm"
            },
            {
                "level": "B",
                "title": "2026 规范软件著作权申请通知",
                "url": "https://zscq.ujs.edu.cn/info/1201/16131.htm"
            }
        ],
        "hard_rules": {
            "paper": "A4",
            "identification_material": "program_and_document",
            "deposit_pages": "first_30_and_last_30_or_all_when_under_60",
            "program_lines_per_page_min": 50,
            "document_lines_per_page_min": 30
        },
        "refresh_required_before_release": True,
        "conflicts": []
    }
    path = case / "01-rule-snapshot/rules.json"
    if write_if_absent(path, snapshot):
        created.append(str(path))

    manifest = {
        "include": ["**/*.py", "**/*.cs", "**/*.java", "**/*.ts", "**/*.tsx", "**/*.vue", "**/*.go", "**/*.cpp", "**/*.h"],
        "exclude": ["**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**", "**/bin/**", "**/obj/**", "**/*.min.js", "**/*.generated.*"],
        "ordered_files": [],
        "selection_policy": {
            "scope": "source files attributable to the applied software",
            "unit": "whole_source_file",
            "preferred_layers": ["entry", "controller", "service", "domain", "algorithm"],
            "rationale": ""
        },
        "file_decisions": [],
        "review": {
            "confirmed_by": "",
            "confirmed_at": "",
            "open_source_boundary_checked": False,
            "generated_code_boundary_checked": False,
            "secret_scan_checked": False
        }
    }
    path = case / "05-source/source-manifest.json"
    if write_if_absent(path, manifest):
        created.append(str(path))

    screenshot_plan = {
        "schema_version": "1.0",
        "base_url": "http://127.0.0.1:3000",
        "output_dir": "screenshots",
        "default_timeout_ms": 15000,
        "browser": {
            "engine": "chromium",
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light"
        },
        "quality": {
            "min_width": 1200,
            "min_height": 700,
            "min_entropy": 0.8,
            "min_content_ratio": 0.002,
            "max_near_white_ratio": 0.997,
            "duplicate_dhash_distance": 1
        },
        "setup": [],
        "captures": [{
            "id": "ui-home",
            "title": "【待申请人确认：首个截图标题】",
            "route": "/",
            "role": "【待申请人确认：操作角色】",
            "evidence_ids": [],
            "ready_selector": "body",
            "assertions": [{"type": "assert_visible", "selector": "body"}],
            "mask": [],
            "hide": [],
            "full_page": False
        }]
    }
    path = case / "02-evidence/screenshot-plan.json"
    if write_if_absent(path, screenshot_plan):
        created.append(str(path))

    preserved: list[dict] = []
    for original in args.preserve:
        source = original.resolve()
        if not source.is_file():
            parser.error(f"Preserve source not found: {source}")
        target = case / "00-original" / source.name
        if target.exists() and sha256_file(target) != sha256_file(source):
            target = target.with_name(f"{target.stem}-{sha256_file(source)[:8]}{target.suffix}")
        if not target.exists():
            shutil.copy2(source, target)
        preserved.append({"source": str(source), "copy": str(target), "sha256": sha256_file(target)})

    save_json(case / "00-original/originals.json", {"created_at": now_iso(), "files": preserved})
    save_json(case / "case.json", {
        "schema_version": "1.0",
        "project_root": str(project),
        "case_root": str(case),
        "created_at": now_iso(),
        "created_files": created
    })
    print(f"CASE_READY={case}")
    print(f"CREATED={len(created)} PRESERVED={len(preserved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
