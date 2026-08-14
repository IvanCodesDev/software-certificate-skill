#!/usr/bin/env python3
"""Verify DOCX structure, evidence coverage, source continuity, and release readiness."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

from common import find_slots, load_json, now_iso, save_json, sha256_file, sha256_text


def docx_metrics(path: Path, facts: dict) -> dict:
    if not path.is_file():
        return {"exists": False, "path": str(path), "issues": ["manual_missing"]}
    issues: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {"word/document.xml", "word/settings.xml", "word/styles.xml", "[Content_Types].xml"}
        missing = sorted(required - names)
        if missing:
            issues.append("missing_docx_parts:" + ",".join(missing))
        document_xml = archive.read("word/document.xml").decode("utf-8", "replace") if "word/document.xml" in names else ""
        settings_xml = archive.read("word/settings.xml").decode("utf-8", "replace") if "word/settings.xml" in names else ""
        styles_xml = archive.read("word/styles.xml").decode("utf-8", "replace") if "word/styles.xml" in names else ""
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", document_xml))
        toc_field = bool(re.search(r"TOC\s+\\o", document_xml))
        toc_h_switch = "\\h" in document_xml
        update_fields = bool(re.search(r"<w:updateFields[^>]*w:val=[\"'](?:true|1)[\"']", settings_xml))
        toc_hyperlinks = len(re.findall(r"<w:hyperlink\b", document_xml))
        heading_1 = len(re.findall(r"w:val=[\"']Heading1[\"']", document_xml))
        heading_2 = len(re.findall(r"w:val=[\"']Heading2[\"']", document_xml))
        images = len([name for name in names if name.startswith("word/media/")])
        page_breaks = len(re.findall(r"w:type=[\"']page[\"']", document_xml))
        slots = find_slots(text)
        consistency = {}
        for key in ("software_full_name", "version", "rightsholder"):
            value = str(facts.get(key, "")).strip()
            if value and not find_slots(value):
                consistency[key] = value in text
                if not consistency[key]:
                    issues.append(f"fact_not_found:{key}")
        if not toc_field:
            issues.append("toc_field_missing")
        if not toc_h_switch:
            issues.append("toc_hyperlink_switch_missing")
        if not update_fields:
            issues.append("update_fields_missing")
        if heading_1 + heading_2 == 0:
            issues.append("outline_headings_missing")
        return {
            "exists": True,
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "valid_zip": True,
            "toc_field": toc_field,
            "toc_hyperlink_switch": toc_h_switch,
            "toc_materialized_hyperlinks": toc_hyperlinks,
            "update_fields": update_fields,
            "heading_1_count": heading_1,
            "heading_2_count": heading_2,
            "image_count": images,
            "explicit_page_breaks": page_breaks,
            "slots": slots,
            "fact_presence": consistency,
            "issues": issues
        }


def source_metrics(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"present": False, "issues": ["source_provenance_missing"]}
    data = load_json(path)
    issues: list[str] = []
    full_pages = int(data.get("full_page_count", 0))
    filing_pages = int(data.get("filing_page_count", 0))
    selection = data.get("selection")
    selected = data.get("selected_source_pages", [])
    if full_pages < 60:
        if selection != "all_under_60_pages" or filing_pages != full_pages:
            issues.append("under_60_selection_incorrect")
    else:
        expected = list(range(1, 31)) + list(range(full_pages - 29, full_pages + 1))
        if selection not in {"first_30_and_last_30", "first_30_and_last_30_separate_volumes"} or selected != expected:
            issues.append("front_back_30_selection_incorrect")
    pages = data.get("pages", [])
    effective_lines = [int(p.get("effective_lines", p.get("line_count", 0))) for p in pages]
    if any(value < int(data.get("lines_per_page", 50)) for value in effective_lines[:-1]):
        issues.append("nonfinal_source_page_under_50_lines")
    review = data.get("manifest_review", {})
    review_ready = all(review.get(key) for key in (
        "confirmed_by", "confirmed_at", "open_source_boundary_checked",
        "generated_code_boundary_checked", "secret_scan_checked"
    ))
    if not review_ready:
        issues.append("source_manifest_review_incomplete")
    if not data.get("ordered_files_confirmed", False):
        issues.append("source_ordered_file_corpus_unconfirmed")
    return {
        "present": True,
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "full_pages": full_pages,
        "filing_pages": filing_pages,
        "selection": selection,
        "effective_line_counts": effective_lines,
        "density_quota": int(data.get("lines_per_page", 50)),
        "last_page_effective_lines": effective_lines[-1] if effective_lines else 0,
        "line_mapping_count": len(data.get("line_mapping", [])),
        "review_ready": review_ready,
        "ordered_files_confirmed": data.get("ordered_files_confirmed", False),
        "issues": issues
    }


def screenshot_metrics(path: Path | None, required: bool = False) -> dict:
    if path is None or not path.is_file():
        return {
            "present": False,
            "required": required,
            "issues": ["screenshot_index_missing"] if required else []
        }
    data = load_json(path)
    captures = data.get("captures", [])
    mode = data.get("mode", "unknown")
    state = data.get("state", "unknown")
    issues: list[str] = []
    verified = 0
    evidence_links = 0
    for index, capture in enumerate(captures):
        prefix = f"capture_{index + 1}:{capture.get('id', 'unknown')}"
        if capture.get("status") != "pass":
            issues.append(f"{prefix}:status_{capture.get('status', 'missing')}")
        shot_value = capture.get("path")
        shot = Path(shot_value) if shot_value else None
        if shot and not shot.is_absolute():
            shot = (path.parent / shot).resolve()
        if not shot or not shot.is_file():
            issues.append(f"{prefix}:file_missing")
            continue
        expected_hash = capture.get("sha256")
        if not expected_hash or sha256_file(shot) != expected_hash:
            issues.append(f"{prefix}:sha256_mismatch")
        metrics = capture.get("metrics", {})
        if int(metrics.get("width", 0)) <= 0 or int(metrics.get("height", 0)) <= 0:
            issues.append(f"{prefix}:dimensions_missing")
        if capture.get("quality_findings"):
            issues.append(f"{prefix}:quality_findings_present")
        if not capture.get("evidence_ids"):
            issues.append(f"{prefix}:evidence_links_missing")
        else:
            evidence_links += len(capture["evidence_ids"])
        if not capture.get("role") or not capture.get("url"):
            issues.append(f"{prefix}:context_missing")
        verified += 1
    if mode == "skip":
        if state != "skipped_by_user":
            issues.append("screenshot_skip_not_explicit")
        if required:
            issues.append("screenshot_skipped_draft_only")
    else:
        if state != "captured":
            issues.append(f"screenshot_state_{state}")
        if not captures:
            issues.append("screenshot_captures_empty")
    summary = data.get("summary", {})
    if summary.get("errors", 0):
        issues.append("screenshot_summary_has_errors")
    if summary.get("quality_warnings", 0):
        issues.append("screenshot_summary_has_quality_warnings")
    return {
        "present": True, "required": required, "mode": mode, "state": state,
        "draft_allowed": mode == "skip" and state == "skipped_by_user",
        "path": str(path.resolve()),
        "sha256": sha256_file(path), "capture_count": len(captures),
        "verified_files": verified, "evidence_links": evidence_links,
        "issues": sorted(set(issues)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--manual", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--mode", choices=["draft", "release"], default="draft")
    parser.add_argument("--source-provenance", type=Path)
    parser.add_argument("--render-metrics", type=Path)
    parser.add_argument("--screenshot-index", type=Path)
    parser.add_argument("--require-screenshots", action="store_true")
    args = parser.parse_args()
    case = args.case.resolve()
    facts_path = case / "01-intake/application-facts.json"
    plan_path = case / "03-storyboard/manual-plan.json"
    graph_path = case / "02-evidence/evidence-graph.json"
    attestation_path = case / "01-intake/authorship-attestation.json"
    facts = load_json(facts_path) if facts_path.exists() else {}
    facts_slots = find_slots(facts)
    facts_hash = sha256_text(json.dumps(facts, ensure_ascii=False, sort_keys=True))

    manual = docx_metrics(args.manual.resolve(), facts)
    plan = load_json(plan_path) if plan_path.exists() else {}
    graph = load_json(graph_path) if graph_path.exists() else {}
    attestation = load_json(attestation_path) if attestation_path.exists() else {}
    provenance_path = args.source_provenance or (case / "06-output/source/source-provenance.json")
    source = source_metrics(provenance_path)
    render = load_json(args.render_metrics) if args.render_metrics and args.render_metrics.exists() else None
    screenshot_path = args.screenshot_index
    if screenshot_path is None:
        discovered = case / "02-evidence/screenshots/screenshot-index.json"
        screenshot_path = discovered if discovered.exists() else None
    screenshot_required = args.require_screenshots or args.mode == "release" or facts.get("screenshot_policy", "required") == "required"
    screenshots = screenshot_metrics(screenshot_path, required=screenshot_required)

    gates = {
        "facts_confirmed": not facts_slots and bool(facts),
        "manual_structure": not manual.get("issues"),
        "toc_jump_ready": manual.get("toc_field", False) and manual.get("toc_hyperlink_switch", False) and manual.get("toc_materialized_hyperlinks", 0) > 0,
        "storyboard_evidence": bool(plan) and not plan.get("evidence_debt_pages") and plan.get("release_ready", False),
        "evidence_graph_present": bool(graph.get("nodes")),
        "screenshots_ready": not screenshots.get("issues"),
        "source_ready": source.get("present", False) and not source.get("issues"),
        "render_ready": render is not None and not render.get("blank_pages") and not render.get("possible_edge_overflow_pages"),
        "attestation_ready": bool(attestation.get("signature_ready"))
    }
    mandatory = ["manual_structure"] if args.mode == "draft" else list(gates)
    result = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "mode": args.mode,
        "case": str(case),
        "facts": {"path": str(facts_path), "sha256": facts_hash, "slots": facts_slots},
        "manual": manual,
        "storyboard": {
            "present": bool(plan),
            "target_pages": plan.get("target_pages"),
            "planned_pages": plan.get("planned_pages"),
            "evidence_debt_count": len(plan.get("evidence_debt_pages", []))
        },
        "evidence": {"present": bool(graph), "summary": graph.get("summary", {})},
        "screenshots": screenshots,
        "source": source,
        "render": render,
        "gates": gates,
        "mandatory_gates": mandatory,
        "release_ready": all(gates[name] for name in mandatory)
    }
    save_json(args.report.resolve(), result)
    print(f"VERIFICATION={args.report.resolve()}")
    print(f"MODE={args.mode} RELEASE_READY={str(result['release_ready']).lower()}")
    print("GATES=" + " ".join(f"{name}:{'PASS' if value else 'FAIL'}" for name, value in gates.items()))
    return 0 if result["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
