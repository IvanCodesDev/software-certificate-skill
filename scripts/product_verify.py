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
from product_model import find_slots


def check(name: str, passed: bool, detail: str, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "severity": severity, "detail": detail}


def docx_text(path: Path) -> str:
    if not path.is_file():
        return ""
    with zipfile.ZipFile(path) as archive:
        return "\n".join(re.sub(r"<[^>]+>", "", archive.read(name).decode("utf-8", "ignore"))
                         for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal", required=True, type=Path)
    parser.add_argument("--quality", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--application-model", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--screenshot-index", required=True, type=Path)
    parser.add_argument("--render-reports", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    formal, quality = args.formal.resolve(), args.quality.resolve()
    facts, business = load_json(args.facts), load_json(args.business)
    application, provenance = load_json(args.application_model), load_json(args.provenance)
    screenshots = load_json(args.screenshot_index) if args.screenshot_index.exists() else {"captures": []}
    software = facts.get("software_full_name", "")
    version = facts.get("version", "")
    expected = ["申请表信息.txt", f"{software}_操作手册.docx", f"{software}_操作手册.pdf", "生成报告.md", "提交材料清单.md"]
    groups = provenance.get("filing_groups", {})
    if "all" in groups:
        expected += [f"{software}-代码(全部).docx", f"{software}-代码(全部).pdf"]
    else:
        expected += [f"{software}-代码(前30页).docx", f"{software}-代码(前30页).pdf",
                     f"{software}-代码(后30页).docx", f"{software}-代码(后30页).pdf"]
    checks: list[dict[str, Any]] = []
    missing = [name for name in expected if not (formal / name).is_file()]
    checks.append(check("正式文件完整性", not missing, "缺少：" + "、".join(missing) if missing else f"{len(expected)}项正式文件齐全"))
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
    render_files = list(args.render_reports.resolve().glob("*.json")) if args.render_reports.exists() else []
    render_models = [load_json(path) for path in render_files]
    render_ok = bool(render_models) and all(item.get("status") == "pass" for item in render_models)
    checks.append(check("PDF转换与逐页渲染", render_ok, f"报告{len(render_models)}份；失败{sum(item.get('status') != 'pass' for item in render_models)}份"))
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
    evidence_ok = bool(business.get("capabilities")) and all(item.get("evidence_ids") for item in business.get("capabilities", []))
    checks.append(check("操作手册业务证据", evidence_ok, f"已确认功能{len(business.get('capabilities', []))}项"))
    captures = screenshots.get("captures", [])
    captures_ok = all(Path(item.get("path", "")).is_file() and sha256_file(Path(item["path"])) == item.get("sha256")
                      for item in captures if item.get("status") == "pass")
    checks.append(check("截图清晰度、来源与章节映射", captures_ok, f"截图{len(captures)}张；模式{screenshots.get('mode', 'unknown')}", "warning"))
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
