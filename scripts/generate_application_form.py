#!/usr/bin/env python3
"""Generate application text using an updateable current-system field rule set."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json


DEFAULT_RULES = Path(__file__).resolve().parents[1] / "assets/rules/application-field-rules.json"
RULES_SCHEMA = Path(__file__).resolve().parents[1] / "assets/schemas/application-field-rules.schema.json"


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def join_values(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(compact(item) for item in value if compact(item))
    return compact(value)


def nested_get(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part, "")
    return current


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
    output = output.rstrip("，、；; ")
    if output and not output.endswith("。"):
        output = output[:max(0, maximum - 1)].rstrip("，、；; ") + "。"
    return output[:maximum], True


def expand_main_functions(business: dict[str, Any], minimum: int) -> tuple[str, bool]:
    text = compact(business.get("main_functions"))
    if len(text) >= minimum:
        return text, False
    parts = [text] if text else []
    for capability in business.get("capabilities", []):
        steps = "；".join(compact(item) for item in capability.get("steps", []) if compact(item))
        restrictions = "；".join(compact(item) for item in capability.get("restrictions", []) if compact(item))
        paragraph = (
            f"{compact(capability.get('name'))}用于{compact(capability.get('purpose'))}。"
            f"使用者从{compact(capability.get('entry'))}进入，可见{compact(capability.get('visible_elements'))}。"
            f"操作过程包括{steps}。完成后{compact(capability.get('success_feedback'))}；"
            f"出现异常时{compact(capability.get('error_feedback'))}。"
            + (f"业务限制包括{restrictions}。" if restrictions else "")
        )
        parts.append(paragraph)
        text = "".join(parts)
        if len(text) >= minimum:
            break
    return "".join(parts), len(parts) > (1 if business.get("main_functions") else 0)


def format_value(value: Any, rule: dict[str, Any]) -> tuple[str, str | None]:
    text = join_values(value)
    format_name = rule.get("format")
    if format_name == "digits":
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value), None
        return (text, None) if re.fullmatch(r"\d+", text) else (text, "format_digits")
    if format_name == "date" and text and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text, "format_date"
    return text, None


def condition_required(rule: dict[str, Any], facts: dict[str, Any]) -> bool:
    condition = rule.get("required_when")
    return bool(rule.get("required")) or bool(condition and nested_get(facts, condition.get("field", "")) == condition.get("equals"))


def validate_rules(rules: dict[str, Any]) -> None:
    try:
        import jsonschema
        jsonschema.Draft202012Validator(load_json(RULES_SCHEMA)).validate(rules)
    except ImportError:
        pass
    keys = [item.get("key") for item in rules.get("fields", [])]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"字段规则存在重复键：{duplicates}")
    for rule in rules.get("fields", []):
        if rule.get("enum") and rule["enum"] not in rules.get("enums", {}):
            raise ValueError(f"字段{rule.get('key')}引用了未定义枚举{rule['enum']}")
        if rule.get("minimum") is not None and rule.get("maximum") is not None and rule["minimum"] > rule["maximum"]:
            raise ValueError(f"字段{rule.get('key')}的最小长度大于最大长度")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-output", required=True, type=Path)
    args = parser.parse_args()
    facts, analysis, business = load_json(args.facts), load_json(args.analysis), load_json(args.business)
    provenance = load_json(args.provenance) if args.provenance and args.provenance.exists() else {}
    rules = load_json(args.rules.resolve())
    validate_rules(rules)
    tech, inferences = analysis.get("technology", {}), analysis.get("field_inferences", {})
    source_lines = provenance.get("original_line_count")
    if source_lines is None and provenance.get("files"):
        source_lines = sum(int(item.get("original_lines", 0)) for item in provenance["files"])
    if source_lines is None:
        source_lines = provenance.get("full_line_count")
    if source_lines is None:
        source_lines = inferences.get("source_line_count", {}).get("suggested_value", "")
    values: dict[str, Any] = {
        **facts,
        "software_classification": business.get("software_classification") or tech.get("project_types"),
        "software_purpose": business.get("software_purpose"), "target_users": business.get("target_users"),
        "industry_domain": business.get("industry_domain"),
        "development_environment": business.get("development_environment") or tech.get("frameworks"),
        "development_tools": business.get("development_tools") or inferences.get("development_tools", {}).get("suggested_value", []),
        "runtime_platform": business.get("runtime_platform") or tech.get("project_types"),
        "runtime_support": business.get("runtime_support"),
        "programming_languages": inferences.get("programming_languages", {}).get("suggested_value", []),
        "source_line_count": source_lines, "development_purpose": business.get("development_purpose"),
        "main_functions": business.get("main_functions"), "technical_features": business.get("technical_features"),
    }
    enums = rules.get("enums", {})
    fields: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for rule in rules.get("fields", []):
        required = condition_required(rule, facts)
        raw = nested_get(values, rule["key"])
        expanded = False
        if rule.get("auto_expand_from_capabilities"):
            raw, expanded = expand_main_functions(business, int(rule.get("minimum", 0)))
        enum_name = rule.get("enum")
        enum_valid = True
        if enum_name:
            enum_values = enums.get(enum_name, {})
            enum_valid = raw in enum_values
            raw = enum_values.get(raw, raw)
        text, format_issue = format_value(raw, rule)
        compressed = False
        if rule.get("maximum") and rule.get("auto_compress"):
            text, compressed = compress(text, int(rule["maximum"]))
        item = {
            "key": rule["key"], "name": rule["label"], "value": text,
            "required": required, "conditional": rule.get("conditional"),
            "characters": len(text), "minimum": rule.get("minimum"), "maximum": rule.get("maximum"),
            "format": rule.get("format"), "enum": enum_name,
            "live_limit_check": bool(rule.get("live_limit_check")),
            "compressed": compressed, "expanded_from_capabilities": expanded,
            "status": "pass" if text or not required else "missing",
        }
        fields.append(item)
        if required and not text:
            issues.append({"field": rule["label"], "code": "required_missing", "severity": "error"})
        if enum_name and not enum_valid:
            issues.append({"field": rule["label"], "code": "enum_invalid", "severity": "error"})
        if format_issue:
            issues.append({"field": rule["label"], "code": format_issue, "severity": "error"})
        if rule.get("minimum") and text and len(text) < int(rule["minimum"]):
            issues.append({"field": rule["label"], "code": "below_minimum",
                           "minimum": rule["minimum"], "actual": len(text),
                           "severity": rule.get("minimum_severity", "review")})
        if rule.get("maximum") and len(text) > int(rule["maximum"]):
            issues.append({"field": rule["label"], "code": "over_limit",
                           "severity": rule.get("maximum_severity", "error")})
    lines: list[str] = []
    for item in fields:
        if item["value"] or item["required"]:
            lines.extend([f"{item['name']}：{item['value']}", ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    blocking = set(rules.get("blocking_severities", ["error"]))
    model = {
        "schema_version": "1.1", "generated_at": now_iso(),
        "rules_snapshot": rules.get("snapshot_date"), "rules_path": str(args.rules.resolve()),
        "rules_source_level": rules.get("source_level"), "dynamic_review_required": rules.get("dynamic_review_required", True),
        "fields": fields, "issues": issues,
        "live_limit_fields": [item["name"] for item in fields if item.get("live_limit_check")],
        "release_ready": not any(item.get("severity") in blocking for item in issues),
        "notes": rules.get("notes", []),
    }
    save_json(args.model_output.resolve(), model)
    print(f"APPLICATION_TEXT={args.output.resolve()}")
    print(f"FIELDS={len(fields)} ISSUES={len(issues)} RELEASE_READY={str(model['release_ready']).lower()}")
    return 0 if model["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
