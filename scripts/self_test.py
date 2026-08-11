#!/usr/bin/env python3
"""Run deterministic smoke tests without requiring Word or LibreOffice."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from common import load_json, now_iso, save_json, sha256_file


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
    for label, capability_count, expected_pages in (("small", 4, 40), ("complex", 30, 66)):
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
    assert large["selection"] == "first_30_and_last_30"
    assert large["filing_page_count"] == 60
    assert large["selected_source_pages"] == expected

    report = {
        "generated_at": now_iso(),
        "status": "pass",
        "checks": {
            "docx_zip": True,
            "real_toc_field": True,
            "toc_hyperlink_switch": True,
            "update_fields": True,
            "manual_has_no_visible_internal_evidence_ids": True,
            "auto_page_plan_small_40": True,
            "auto_page_plan_complex_66": True,
            "under_60_submits_all": True,
            "at_least_60_selects_front_back_30": True,
            "source_provenance": True
        },
        "artifacts": {
            "manual": {"path": str(manual), "sha256": sha256_file(manual)},
            "small_provenance": str(small_out / "source-provenance.json"),
            "large_provenance": str(large_out / "source-provenance.json")
        },
        "commands": commands
    }
    report_path = work / "self-test.json"
    save_json(report_path, report)
    print(f"SELF_TEST=PASS REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
