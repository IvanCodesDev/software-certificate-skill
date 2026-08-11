#!/usr/bin/env python3
"""Generate copy-ready application-form text and an internal field validation model."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json

DEVELOPMENT = {"independent": "单独开发", "cooperative": "合作开发", "commissioned": "委托开发", "assigned": "下达任务开发"}
NATURE = {"original": "原创", "modified": "修改"}
PUBLICATION = {"published": "已发表", "unpublished": "未发表"}
ACQUISITION = {"original": "原始取得", "successive": "继受取得"}
SCOPE = {"all": "全部权利", "partial": "部分权利"}
HOLDER_TYPE = {"natural_person": "自然人", "legal_person": "法人", "other_organization": "其他组织"}
FIELD_LIMITS = {
    "开发目的": 500, "面向领域": 500, "主要功能": 1300, "技术特点": 500,
    "运行支撑环境": 500, "开发环境": 500,
}


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def join_values(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(compact(item) for item in value if compact(item))
    return compact(value)


def compress(value: str, maximum: int) -> tuple[str, bool]:
    text = compact(value)
    if len(text) <= maximum:
        return text, False
    sentences = [item.strip() for item in re.split(r"(?<=[。；;])", text) if item.strip()]
    output = ""
    for sentence in sentences:
        if len(output + sentence) > maximum:
            break
        output += sentence
    if not output:
        output = text[:maximum]
    return output.rstrip("，、；; ") + ("。" if not output.endswith("。") else ""), True


def field(name: str, value: Any, required: bool = True, conditional: str | None = None) -> dict[str, Any]:
    text = join_values(value)
    maximum = FIELD_LIMITS.get(name)
    compressed = False
    if maximum:
        text, compressed = compress(text, maximum)
    return {
        "name": name, "value": text, "required": required, "conditional": conditional,
        "characters": len(text), "maximum": maximum, "compressed": compressed,
        "status": "pass" if text or not required else "missing",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-output", required=True, type=Path)
    args = parser.parse_args()
    facts = load_json(args.facts)
    analysis = load_json(args.analysis)
    business = load_json(args.business)
    provenance = load_json(args.provenance) if args.provenance and args.provenance.exists() else {}
    holder = facts.get("rightsholder", {})
    publication = facts.get("publication", {})
    rights_scope = facts.get("rights_scope", {})
    tech = analysis.get("technology", {})
    inferences = analysis.get("field_inferences", {})
    languages = inferences.get("programming_languages", {}).get("suggested_value", [])
    source_lines = provenance.get("full_line_count") or inferences.get("source_line_count", {}).get("suggested_value", "")
    fields = [
        field("软件全称", facts.get("software_full_name")),
        field("软件简称", facts.get("software_short_name", ""), required=False),
        field("版本号", facts.get("version")),
        field("著作权人类型", HOLDER_TYPE.get(holder.get("type"), holder.get("type"))),
        field("著作权人名称", holder.get("name")),
        field("证件类型", holder.get("id_type")),
        field("证件号码", holder.get("id_number")),
        field("开发完成日期", facts.get("completion_date")),
        field("开发方式", DEVELOPMENT.get(facts.get("development_mode"), facts.get("development_mode"))),
        field("软件说明", NATURE.get(facts.get("software_nature"), facts.get("software_nature"))),
        field("发表状态", PUBLICATION.get(publication.get("status"), publication.get("status"))),
        field("首次发表日期", publication.get("first_publication_date", ""), required=publication.get("status") == "published",
              conditional="仅已发表软件填写"),
        field("权利取得方式", ACQUISITION.get(facts.get("rights_acquisition"), facts.get("rights_acquisition"))),
        field("权利范围", SCOPE.get(rights_scope.get("type"), rights_scope.get("type"))),
        field("部分权利说明", rights_scope.get("detail", ""), required=rights_scope.get("type") == "partial",
              conditional="仅部分权利填写"),
        field("权属补充说明", facts.get("ownership_notes", ""), required=False),
        field("软件分类", join_values(business.get("software_classification") or tech.get("project_types"))),
        field("软件用途", business.get("software_purpose")),
        field("目标用户", business.get("target_users")),
        field("面向行业", business.get("industry_domain")),
        field("开发环境", business.get("development_environment") or join_values(tech.get("frameworks"))),
        field("开发工具", business.get("development_tools") or join_values(inferences.get("development_tools", {}).get("suggested_value", []))),
        field("运行平台", business.get("runtime_platform") or join_values(tech.get("project_types"))),
        field("运行支撑环境", business.get("runtime_support")),
        field("编程语言", join_values(languages)),
        field("源程序量", f"{source_lines} 行" if source_lines != "" else ""),
        field("开发目的", business.get("development_purpose")),
        field("面向领域", business.get("industry_domain")),
        field("主要功能", business.get("main_functions")),
        field("技术特点", business.get("technical_features")),
    ]
    issues = []
    for item in fields:
        if item["status"] == "missing":
            issues.append({"field": item["name"], "code": "required_missing"})
        if item["maximum"] and item["characters"] > item["maximum"]:
            issues.append({"field": item["name"], "code": "over_limit"})
    main_function = next(item for item in fields if item["name"] == "主要功能")
    if 0 < main_function["characters"] < 500:
        issues.append({"field": "主要功能", "code": "below_current_form_guidance_500", "severity": "review"})
    lines = []
    for item in fields:
        if item["value"] or item["required"]:
            lines.extend([f"{item['name']}：{item['value']}", ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    model = {
        "schema_version": "1.0", "generated_at": now_iso(),
        "rules_snapshot": "2026-08-11", "fields": fields, "issues": issues,
        "release_ready": not any(item.get("code") in {"required_missing", "over_limit"} for item in issues),
        "notes": ["字段顺序用于复制填写；提交当日仍以当前登记系统表单为准。",
                  "500–1300 字为当前办理信号，不升级为通用法定要求。"],
    }
    save_json(args.model_output.resolve(), model)
    print(f"APPLICATION_TEXT={args.output.resolve()}")
    print(f"FIELDS={len(fields)} ISSUES={len(issues)} RELEASE_READY={str(model['release_ready']).lower()}")
    return 0 if model["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
