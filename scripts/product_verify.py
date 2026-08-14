#!/usr/bin/env python3
"""Run consolidated release checks for the beginner-facing product workflow."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json, sha256_file
from product_model import find_slots, safe_filename


def check(name: str, passed: bool, detail: str, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "severity": severity, "detail": detail}


def docx_text(path: Path) -> str:
    if not path.is_file():
        return ""
    with zipfile.ZipFile(path) as archive:
        return "\n".join(re.sub(r"<[^>]+>", "", archive.read(name).decode("utf-8", "ignore"))
                         for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml"))


def toc_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "missing"}
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml").decode("utf-8", "replace")
        settings = archive.read("word/settings.xml").decode("utf-8", "replace")
    return {
        "toc_field": bool(re.search(r"TOC\s+\\o", document)),
        "hyperlink_switch": "\\h" in document,
        "materialized_hyperlinks": len(re.findall(r"<w:hyperlink\b", document)),
        "bookmarks": len(re.findall(r"<w:bookmarkStart\b", document)),
        "page_reference_fields": len(re.findall(r"PAGEREF\s+_SoftCertToc", document)),
        "update_fields": bool(re.search(r"<w:updateFields[^>]*w:val=[\"'](?:true|1)[\"']", settings)),
    }


def backend_side_coverage(provenance: dict[str, Any]) -> tuple[bool, str]:
    """Every filing volume of a both-sided corpus must show backend source.

    Only the first/last 30 pages are filed for large corpora, so backend
    files can silently drop out of the delivered pages even when selected.
    Applies to analyzer-confirmed fullstack projects AND corpora the
    selector balanced because both sides were detected in the repository.
    """
    sides = {item.get("path"): item.get("side") for item in provenance.get("file_decisions", [])}
    file_records = provenance.get("files", [])
    composed_backend = sum(1 for item in file_records if sides.get(item.get("path")) == "backend")
    if not composed_backend:
        return False, "项目含后端实现，但代码材料未纳入任何后端源文件；须重新选择源码"
    missing: list[str] = []
    for name, group in provenance.get("filing_groups", {}).items():
        first = group.get("first_output_line") or 0
        last = group.get("last_output_line") or 0
        volume_sides = {sides.get(item.get("path")) for item in file_records
                        if item.get("output_end_line", 0) >= first and item.get("output_start_line", 0) <= last}
        if "backend" not in volume_sides:
            missing.append(name)
    if missing:
        return False, ("以下交存卷不含后端源码：" + "、".join(sorted(missing))
                       + "；须调整源码混排后重新生成")
    return True, f"{composed_backend}个后端源文件已纳入，前后交存卷均含后端实现"


def screenshot_release_check(index_path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    mode, state = data.get("mode", "unknown"), data.get("state", "unknown")
    captures = data.get("captures", [])
    if mode == "skip" and state == "skipped_by_user":
        return False, "用户明确跳过截图；只允许生成带占位符的草稿"
    if state != "captured":
        return False, f"截图状态{state}；模式{mode}"
    if not captures:
        return False, f"截图列表为空；模式{mode}"
    issues: list[str] = []
    for item in captures:
        shot_value = item.get("path")
        shot = Path(shot_value) if shot_value else None
        if shot and not shot.is_absolute():
            shot = (index_path.parent / shot).resolve()
        if item.get("status") != "pass":
            issues.append(f"{item.get('id')}:status")
        if not shot or not shot.is_file():
            issues.append(f"{item.get('id')}:file")
            continue
        if sha256_file(shot) != item.get("sha256"):
            issues.append(f"{item.get('id')}:hash")
        metrics = item.get("metrics", {})
        if int(metrics.get("width", 0)) < 900 or int(metrics.get("height", 0)) < 500:
            issues.append(f"{item.get('id')}:dimensions")
        if item.get("quality_findings"):
            issues.append(f"{item.get('id')}:quality")
        if not item.get("evidence_ids"):
            issues.append(f"{item.get('id')}:mapping")
        if not item.get("role") or not item.get("url"):
            issues.append(f"{item.get('id')}:context")
    summary = data.get("summary", {})
    if summary.get("errors") or summary.get("quality_warnings") or summary.get("missing_planned"):
        issues.append("summary")
    return not issues, (f"截图{len(captures)}张，文件、哈希、清晰度和章节映射通过" if not issues
                        else "问题：" + "、".join(issues[:12]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal", required=True, type=Path)
    parser.add_argument("--quality", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--manual-content", required=True, type=Path)
    parser.add_argument("--application-model", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--screenshot-index", required=True, type=Path)
    parser.add_argument("--render-reports", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    formal, quality = args.formal.resolve(), args.quality.resolve()
    facts, business = load_json(args.facts), load_json(args.business)
    manual_content = load_json(args.manual_content)
    application, provenance = load_json(args.application_model), load_json(args.provenance)
    screenshots = load_json(args.screenshot_index) if args.screenshot_index.exists() else {"captures": []}
    software = facts.get("software_full_name", "")
    software_filename = safe_filename(software)
    version = facts.get("version", "")
    expected = ["申请表信息.txt", f"{software_filename}_操作手册.docx", f"{software_filename}_操作手册.pdf"]
    groups = provenance.get("filing_groups", {})
    if "all" in groups:
        expected += [f"{software_filename}-代码(全部).docx", f"{software_filename}-代码(全部).pdf"]
    else:
        expected += [f"{software_filename}-代码(前30页).docx", f"{software_filename}-代码(前30页).pdf",
                     f"{software_filename}-代码(后30页).docx", f"{software_filename}-代码(后30页).pdf"]
    checks: list[dict[str, Any]] = []
    missing = [name for name in expected if not (formal / name).is_file()]
    actual = {path.name for path in formal.iterdir() if path.is_file()}
    unexpected = sorted(actual - set(expected))
    package_ok = not missing and not unexpected
    package_detail = []
    if missing:
        package_detail.append("缺少：" + "、".join(missing))
    if unexpected:
        package_detail.append("非交付文件：" + "、".join(unexpected))
    checks.append(check("正式文件完整性与最小交付面", package_ok,
                        "；".join(package_detail) if package_detail else f"{len(expected)}项交付文件齐全且无内部文件"))
    application_text = (formal / "申请表信息.txt").read_text(encoding="utf-8") if (formal / "申请表信息.txt").is_file() else ""
    checks.append(check("申请表必填字段与长度", bool(application.get("release_ready")), f"问题数：{len(application.get('issues', []))}"))
    checks.append(check("单一事实源一致性", bool(software and version and software in application_text and version in application_text),
                        "软件名称与版本已贯穿申请表" if software in application_text and version in application_text else "申请表名称或版本不一致"))
    docx_files = list(formal.glob("*.docx"))
    valid_docx, docx_errors = True, []
    for path in docx_files:
        try:
            text = docx_text(path)
            if not text or software not in text or version not in text:
                valid_docx = False
                docx_errors.append(path.name)
        except Exception:
            valid_docx = False
            docx_errors.append(path.name)
    checks.append(check("DOCX结构与页眉事实", valid_docx and bool(docx_files), "异常：" + "、".join(docx_errors) if docx_errors else f"{len(docx_files)}个DOCX结构可读"))
    manual_docx_path = formal / f"{software_filename}_操作手册.docx"
    toc = toc_metrics(manual_docx_path)
    toc_ok = all(toc.get(key) for key in ("toc_field", "hyperlink_switch", "materialized_hyperlinks",
                                           "bookmarks", "page_reference_fields", "update_fields"))
    checks.append(check("智能目录与可点击跳转", toc_ok,
                        f"TOC域={toc.get('toc_field')}；超链接={toc.get('materialized_hyperlinks', 0)}；"
                        f"书签={toc.get('bookmarks', 0)}；动态页码={toc.get('page_reference_fields', 0)}"))
    render_files = list(args.render_reports.resolve().glob("*.json")) if args.render_reports.exists() else []
    render_models = [load_json(path) for path in render_files]
    render_ok = bool(render_models) and all(item.get("status") == "pass" for item in render_models)
    checks.append(check("PDF转换与逐页渲染", render_ok, f"报告{len(render_models)}份；失败{sum(item.get('status') != 'pass' for item in render_models)}份"))
    manual_render = next((item for item in render_models if Path(item.get("input", "")).name == "manual.docx"), {})
    manual_pdf_pages = int(manual_render.get("pdf_pages", 0))
    logical_sections = int(manual_content.get("generation", {}).get("logical_sections", 0))
    preferred_applicable = logical_sections >= 40
    manual_length_ok = manual_pdf_pages > 0 and manual_pdf_pages <= 66 and (
        not preferred_applicable or manual_pdf_pages >= 40
    )
    length_reason = (
        f"实际PDF {manual_pdf_pages}页；内容逻辑页{logical_sections}页；"
        + ("按丰富度适用40–60页优选区间" if preferred_applicable else "小型项目按证据量缩短，不填充无效内容")
    )
    checks.append(check("操作手册页数与内容丰富度", manual_length_ok, length_reason))
    mapping = provenance.get("line_mapping", [])
    sample_ok = bool(mapping) and all(item.get("file") and item.get("source_line") for item in mapping[::max(1, len(mapping)//20)])
    checks.append(check("代码真实性抽样追溯", sample_ok, f"映射记录{len(mapping)}条"))
    group_ok = False
    if "all" in groups:
        group_ok = groups["all"].get("page_count") == provenance.get("full_page_count") and provenance.get("full_page_count", 0) < 60
    elif {"front_30", "back_30"} <= set(groups):
        front, back = groups["front_30"], groups["back_30"]
        group_ok = front.get("page_count") == 30 and back.get("page_count") == 30 and \
                   front.get("logical_source_pages") == list(range(1, 31)) and \
                   back.get("logical_source_pages", [])[-1:] == [provenance.get("full_page_count")]
    checks.append(check("代码页数与前后卷连续性", group_ok, f"总逻辑页{provenance.get('full_page_count')}；分卷{list(groups)}"))
    # Reviewers count effective source lines — a wrapped long line is still
    # one line. Every page but the last must carry the full quota.
    page_meta = provenance.get("pages", [])
    quota = int(provenance.get("lines_per_page", 50))
    effective = [int(item.get("effective_lines", item.get("line_count", 0))) for item in page_meta]
    short_pages = [index + 1 for index, value in enumerate(effective[:-1]) if value < quota]
    dense_ok = bool(effective) and not short_pages
    checks.append(check("代码每页有效行数", dense_ok,
                        f"每页{quota}条有效源码行；末页{effective[-1] if effective else 0}条（末页例外）"
                        if dense_ok else f"不足{quota}条的页：{short_pages[:12]}"))
    # A frontend-only repository must not present itself as a full system:
    # the form has to acknowledge the external backend it depends on.
    architecture = str(provenance.get("selection_policy", {}).get("architecture_scope", ""))
    side_balance = provenance.get("selection_policy", {}).get("side_balance")
    if architecture == "frontend_only":
        described = " ".join(str(business.get(key, "")) for key in
                             ("runtime_support", "runtime_platform", "main_functions",
                              "technical_features", "software_purpose"))
        acknowledged = bool(re.search(r"后端|服务端|服务器|接口|API", described))
        checks.append(check("申请范围与源码架构一致", acknowledged,
                            "纯前端仓库：材料仅含前端源码，申请表已注明需配合后端/接口服务" if acknowledged
                            else "纯前端仓库，但申请表未注明依赖后端/接口服务，申请范围存在误导风险"))
    elif architecture == "fullstack" or side_balance:
        # Covers analyzer-confirmed fullstack AND the selector's fail-safe
        # balancing for unclear scopes, so a misread scope can never ship
        # frontend-only code materials for a repository holding a backend.
        covered, coverage_detail = backend_side_coverage(provenance)
        checks.append(check("申请范围与源码架构一致", covered, coverage_detail))
    elif architecture and architecture != "unclassified":
        checks.append(check("申请范围与源码架构一致", True, f"架构范围：{architecture}"))
    evidence_ok = bool(business.get("capabilities")) and all(item.get("evidence_ids") for item in business.get("capabilities", []))
    checks.append(check("操作手册业务证据", evidence_ok, f"已确认功能{len(business.get('capabilities', []))}项"))
    manual_quality = manual_content.get("content_quality", {})
    checks.append(check("操作手册内容厚度与非模板化", manual_quality.get("status") == "pass",
                        f"问题数：{len(manual_quality.get('issues', []))}；重复句式比例：{manual_quality.get('metrics', {}).get('repeated_sentence_ratio', 'unknown')}"))
    captures_ok, capture_detail = screenshot_release_check(args.screenshot_index.resolve(), screenshots)
    checks.append(check("截图清晰度、来源与章节映射", captures_ok, capture_detail))
    slots = find_slots({"facts": facts, "business": business, "application": application})
    checks.append(check("待确认占位符清零", not slots, "无占位符" if not slots else "、".join(slots)))
    sensitive = provenance.get("manifest_review", {}).get("secret_findings", [])
    checks.append(check("敏感信息与第三方风险", not sensitive, f"未进入材料的命中记录{len(sensitive)}项" if sensitive else "未发现进入材料的敏感项"))
    errors = [item for item in checks if item["status"] == "fail" and item["severity"] == "error"]
    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "software_full_name": software,
        "version": version, "checks": checks, "summary": {"pass": sum(item["status"] == "pass" for item in checks),
        "fail": sum(item["status"] == "fail" for item in checks), "blocking": len(errors)},
        "release_ready": not errors, "formal_files": [{"name": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
                                                       for path in sorted(formal.iterdir()) if path.is_file()],
    }
    save_json(args.output.resolve(), report)
    print(f"PRODUCT_VERIFICATION={args.output.resolve()}")
    print(f"RELEASE_READY={str(report['release_ready']).lower()} PASS={report['summary']['pass']} FAIL={report['summary']['fail']}")
    return 0 if report["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
