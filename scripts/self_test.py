#!/usr/bin/env python3
"""Run deterministic smoke tests without requiring Word or LibreOffice."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from capture_web_screenshots import image_metrics, quality_findings
from common import load_json, now_iso, save_json, sha256_file
from verify_package import screenshot_metrics as verify_screenshot_metrics


def run(command: list[str]) -> dict:
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return {"command": command, "exit_code": result.returncode, "output": result.stdout.strip()}


def source_fixture(root: Path, name: str, line_count: int) -> tuple[Path, Path]:
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    source = project / "engine.py"
    source.write_text("\n".join(f"value_{i} = process_item({i})" for i in range(1, line_count + 1)) + "\n", encoding="utf-8")
    manifest = project / "manifest.json"
    save_json(manifest, {
        "include": ["**/*.py"],
        "exclude": [],
        "ordered_files": ["engine.py"],
        "review": {
            "confirmed_by": "self-test",
            "confirmed_at": now_iso(),
            "open_source_boundary_checked": True,
            "generated_code_boundary_checked": True,
            "secret_scan_checked": True
        }
    })
    return project, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    skill = Path(__file__).resolve().parents[1]
    work = args.workdir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    commands: list[dict] = []

    commands.append(run([
        sys.executable, str(skill / "scripts/validate_skill.py"), str(skill)
    ]))

    screenshot_plan = work / "screenshot-plan.json"
    save_json(screenshot_plan, {
        "schema_version": "1.0",
        "base_url": "http://127.0.0.1:4173",
        "browser": {"viewport": {"width": 1440, "height": 900}},
        "captures": [{
            "id": "dashboard-overview", "title": "工作台概览", "route": "/dashboard",
            "role": "管理员", "evidence_ids": ["CAP-dashboard"],
            "ready_selector": "[data-testid='dashboard']", "full_page": False
        }]
    })
    commands.append(run([
        sys.executable, str(skill / "scripts/capture_web_screenshots.py"),
        "--plan", str(screenshot_plan), "--validate-only"
    ]))

    screenshot_fixture = work / "screenshot-fixture.png"
    image = Image.new("RGB", (1440, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1439, 72), fill="#303030")
    draw.rectangle((48, 120, 1390, 840), fill="#eeeeee", outline="#555555", width=3)
    draw.text((80, 160), "Software evidence dashboard", fill="black")
    image.save(screenshot_fixture)
    screenshot_metrics = image_metrics(screenshot_fixture)
    assert not quality_findings(screenshot_metrics, {
        "min_width": 1200, "min_height": 700, "min_entropy": 0.1,
        "min_content_ratio": 0.001, "max_near_white_ratio": 0.99
    })
    screenshot_index = work / "screenshot-index.json"
    save_json(screenshot_index, {
        "schema_version": "1.0", "generated_at": now_iso(),
        "summary": {"requested": 1, "completed": 1, "passed": 1,
                    "quality_warnings": 0, "errors": 0},
        "captures": [{
            "id": "dashboard-overview", "title": "工作台概览", "status": "pass",
            "path": str(screenshot_fixture), "sha256": sha256_file(screenshot_fixture),
            "url": "http://127.0.0.1:4173/dashboard", "role": "管理员",
            "evidence_ids": ["CAP-dashboard"], "metrics": screenshot_metrics,
            "quality_findings": []
        }]
    })
    assert not verify_screenshot_metrics(screenshot_index, required=True)["issues"]

    adapters = work / "agent-adapters"
    adapter_report = work / "agent-adapters.json"
    commands.append(run([
        sys.executable, str(skill / "scripts/install_agent_skill.py"),
        "--source", str(skill), "--platform", "all", "--scope", "project",
        "--project", str(adapters), "--force", "--report", str(adapter_report)
    ]))
    expected_adapters = [
        adapters / ".claude/skills/software-certificate-skill/SKILL.md",
        adapters / ".cursor/skills/software-certificate-skill/SKILL.md",
        adapters / ".opencode/skills/software-certificate-skill/SKILL.md",
        adapters / ".agents/skills/software-certificate-skill/SKILL.md",
        adapters / "AGENTS.md",
        adapters / ".qoder/rules/software-certificate-skill.md",
        adapters / ".trae/rules/software-certificate-skill.md",
    ]
    assert all(path.is_file() for path in expected_adapters)
    assert set(load_json(adapter_report)["platforms"]) == {
        "codex", "claude-code", "cursor", "opencode", "workbuddy", "qoderwork", "traework"
    }

    manual = work / "manual.docx"
    commands.append(run([
        sys.executable, str(skill / "scripts/build_manual.py"),
        "--input", str(skill / "assets/examples/manual-input.example.json"),
        "--theme", str(skill / "assets/themes/standard-filing-gray.json"),
        "--output", str(manual)
    ]))
    with zipfile.ZipFile(manual) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8", "replace")
        settings_xml = archive.read("word/settings.xml").decode("utf-8", "replace")
        assert "TOC \\o" in document_xml and "\\h" in document_xml
        assert "updateFields" in settings_xml
        assert not any(token in document_xml for token in ("SOFTWARE COPYRIGHT", "PRODUCT EVIDENCE", "UI-login", "STEP-login"))

    facts_path = work / "facts.json"
    save_json(facts_path, {
        "software_full_name": "自检软件",
        "software_short_name": "自检软件",
        "version": "V1.0",
        "rightsholder": "自检申请人",
        "publication_status": "unpublished"
    })
    for label, capability_count, expected_pages in (("small", 4, 14), ("complex", 30, 40)):
        graph_path = work / f"evidence-{label}.json"
        save_json(graph_path, {
            "schema_version": "1.0",
            "project": {"name": label},
            "nodes": [
                {"id": f"CAP-{index}", "type": "capability_candidate", "name": f"功能{index}",
                 "status": "human_confirmed", "evidence_ids": [f"FILE-{index}"]}
                for index in range(capability_count)
            ],
            "edges": [],
            "summary": {}
        })
        plan_path = work / f"plan-{label}.json"
        commands.append(run([
            sys.executable, str(skill / "scripts/plan_manual.py"),
            "--facts", str(facts_path), "--evidence", str(graph_path),
            "--output", str(plan_path), "--target-pages", "auto"
        ]))
        assert load_json(plan_path)["planned_pages"] == expected_pages

    small_project, small_manifest = source_fixture(work, "source-small", 120)
    small_out = work / "source-small-output"
    commands.append(run([
        sys.executable, str(skill / "scripts/compose_code.py"),
        "--project", str(small_project), "--manifest", str(small_manifest),
        "--output-dir", str(small_out)
    ]))
    small = load_json(small_out / "source-provenance.json")
    assert small["selection"] == "all_under_60_pages"
    assert small["full_page_count"] == small["filing_page_count"] == 3

    large_project, large_manifest = source_fixture(work, "source-large", 3051)
    large_out = work / "source-large-output"
    commands.append(run([
        sys.executable, str(skill / "scripts/compose_code.py"),
        "--project", str(large_project), "--manifest", str(large_manifest),
        "--output-dir", str(large_out)
    ]))
    large = load_json(large_out / "source-provenance.json")
    expected = list(range(1, 31)) + list(range(large["full_page_count"] - 29, large["full_page_count"] + 1))
    assert large["selection"] == "first_30_and_last_30_separate_volumes"
    assert large["filing_page_count"] == 60
    assert large["filing_groups"]["front_30"]["logical_source_pages"] == expected[:30]
    assert large["filing_groups"]["back_30"]["logical_source_pages"] == expected[30:]
    assert (large_out / "source-front-30.docx").is_file()
    assert (large_out / "source-back-30.docx").is_file()

    report = {
        "generated_at": now_iso(),
        "status": "pass",
        "checks": {
            "docx_zip": True,
            "real_toc_field": True,
            "toc_hyperlink_switch": True,
            "update_fields": True,
            "manual_has_no_visible_internal_evidence_ids": True,
            "content_based_page_plan_small": True,
            "content_based_page_plan_complex": True,
            "under_60_submits_all": True,
            "at_least_60_selects_front_back_30": True,
            "source_provenance": True,
            "platform_independent_skill_validation": True,
            "screenshot_plan_validation": True,
            "screenshot_image_quality_metrics": True,
            "screenshot_release_gate": True,
            "multi_agent_adapter_install": True,
        },
        "artifacts": {
            "manual": {"path": str(manual), "sha256": sha256_file(manual)},
            "small_provenance": str(small_out / "source-provenance.json"),
            "large_provenance": str(large_out / "source-provenance.json"),
            "screenshot_fixture": {"path": str(screenshot_fixture), "sha256": sha256_file(screenshot_fixture)},
            "screenshot_index": str(screenshot_index),
            "agent_adapter_report": str(adapter_report),
        },
        "commands": commands
    }
    report_path = work / "self-test.json"
    save_json(report_path, report)
    print(f"SELF_TEST=PASS REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
