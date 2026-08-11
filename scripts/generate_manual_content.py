#!/usr/bin/env python3
"""Build an adaptive, evidence-led Chinese software operation manual."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json
from product_model import find_slots


DEFAULT_QUALITY_RULES = Path(__file__).resolve().parents[1] / "assets/rules/manual-content-quality.json"


PROFILE_COPY = {
    "query": {
        "chapter_lead": "围绕查询条件、数据范围和结果字段说明实际使用方法。",
        "procedure_title": "筛选、查看与记录核对",
        "procedure_lead": "先限定查询范围，再核对列表、详情或统计结果，避免把无数据误判为系统异常。",
        "detail_title": "结果字段与数据范围",
        "detail_lead": "查询结果应结合权限范围、筛选条件和页面字段共同判读。",
    },
    "approval": {
        "chapter_lead": "按照待处理对象、核验依据、状态变化和处理反馈组织本节内容。",
        "procedure_title": "业务核验与状态确认",
        "procedure_lead": "处理前核对对象和当前状态，提交后再确认状态变化与后续可执行动作。",
        "detail_title": "状态变化与处理规则",
        "detail_lead": "状态结果由当前数据、业务规则和处理动作共同决定。",
    },
    "analysis": {
        "chapter_lead": "说明分析范围、输入条件、结果字段及其业务含义。",
        "procedure_title": "数据范围设置与分析执行",
        "procedure_lead": "先确认数据范围和指标口径，再执行分析并核对输出内容。",
        "detail_title": "指标口径与结果判读",
        "detail_lead": "分析结果用于辅助业务判断，阅读时应同时关注范围、字段和当前状态。",
    },
    "monitoring": {
        "chapter_lead": "说明运行状态、观察信号和异常处置入口。",
        "procedure_title": "状态查看与异常定位",
        "procedure_lead": "从当前状态出发核对关键信号，再根据页面反馈定位异常项。",
        "detail_title": "状态信号与处置依据",
        "detail_lead": "监控结果应结合时间范围、状态字段和实际业务对象进行判断。",
    },
    "configuration": {
        "chapter_lead": "说明配置对象、参数边界、生效范围和保存后的反馈。",
        "procedure_title": "参数设置与生效确认",
        "procedure_lead": "修改前确认作用范围，保存后通过页面状态或结果字段核对是否生效。",
        "detail_title": "参数边界与配置规则",
        "detail_lead": "配置值必须满足已确认的业务规则，并在对应范围内生效。",
    },
    "file_processing": {
        "chapter_lead": "说明文件要求、处理动作、输出结果和失败反馈。",
        "procedure_title": "文件检查、处理与输出",
        "procedure_lead": "先检查文件与参数，再执行处理并核对输出位置和结果状态。",
        "detail_title": "文件要求与结果校验",
        "detail_lead": "文件处理结果以格式、范围、输出内容和页面反馈为共同依据。",
    },
    "data_entry": {
        "chapter_lead": "围绕录入对象、字段要求、保存校验和结果反馈说明实际操作。",
        "procedure_title": "新增、编辑与保存",
        "procedure_lead": "依次完成对象定位、字段填写、页面校验和保存结果确认。",
        "detail_title": "字段要求与保存校验",
        "detail_lead": "录入内容应满足必填、关联和取值范围等已确认规则。",
    },
    "generic": {
        "chapter_lead": "根据真实入口、界面控件、操作动作和完成反馈说明本项功能。",
        "procedure_title": "操作步骤与界面反馈",
        "procedure_lead": "按照页面或命令返回的实际反馈逐步完成任务，并核对最终状态。",
        "detail_title": "权限范围与完成结果",
        "detail_lead": "完成结果以当前角色、可见范围和界面反馈为准。",
    },
}


def screenshot_map(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item.get("id"): item for item in index.get("captures", []) if item.get("id") and item.get("path")}


def text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if value else []


def joined(value: Any, fallback: str = "以页面实际提示为准") -> str:
    return "；".join(text_list(value)) or fallback


def evidence_purpose(capability: dict[str, Any], role: str) -> str:
    purpose = str(capability.get("purpose", "")).strip()
    generic = re.search(r"用于支撑.+实际业务处理与结果核对", purpose)
    if purpose and not generic:
        return purpose
    outputs = joined(capability.get("outputs"), capability.get("success_feedback", "完成当前操作"))
    states = joined(capability.get("state_changes"), capability.get("success_feedback", "页面显示处理结果"))
    return f"{capability['name']}面向{role}，用于形成或核对{outputs}；操作完成后，{states}。"


def effective_prerequisites(capability: dict[str, Any]) -> list[str]:
    """Remove a common login circularity without inventing product behavior."""
    items = text_list(capability.get("prerequisites"))
    access_text = f"{capability.get('name', '')} {capability.get('entry', '')}"
    if "登录" not in access_text:
        return items
    cleaned: list[str] = []
    for item in items:
        if "已登录" in item:
            if "登录" in access_text:
                replacement = "已取得系统分配的有效账号和认证信息"
            else:
                replacement = f"当前账号已获准进入“{capability.get('name', '本功能')}”并执行本节操作"
            if replacement not in cleaned:
                cleaned.append(replacement)
        else:
            cleaned.append(item)
    return cleaned


def operation_profile(capability: dict[str, Any]) -> str:
    explicit = capability.get("operation_type")
    if explicit in PROFILE_COPY:
        return str(explicit)
    text = " ".join(str(capability.get(key, "")) for key in ("name", "purpose", "entry", "visible_elements"))
    groups = [
        ("approval", "审批 审核 复核 确认 提交"), ("query", "查询 检索 筛选 搜索 列表"),
        ("analysis", "分析 统计 报表 预测 指标 图表"), ("monitoring", "监控 告警 日志 状态 运行"),
        ("configuration", "配置 设置 参数 规则 模板"), ("file_processing", "导入 导出 文件 上传 下载 批量"),
        ("data_entry", "新增 编辑 录入 填写 创建 登记"),
    ]
    for name, keywords in groups:
        if any(keyword in text for keyword in keywords.split()):
            return name
    return "generic"


def table_rows(capability: dict[str, Any], business: dict[str, Any], profile: str) -> tuple[list[str], list[list[str]]]:
    role = str(capability.get("actor") or business["target_users"])
    entry = str(capability["entry"])
    visible = str(capability["visible_elements"])
    scope = str(capability.get("data_scope") or joined(capability.get("restrictions")))
    if profile == "query":
        return ["查询要素", "实际说明"], [["使用角色", role], ["查询入口", entry], ["筛选与结果区", visible], ["数据范围", scope]]
    if profile == "approval":
        return ["处理要素", "实际说明"], [["处理角色", role], ["业务入口", entry], ["核验内容", visible], ["状态变化", joined(capability.get("state_changes"), capability["success_feedback"])]]
    if profile in {"analysis", "monitoring"}:
        return ["观察要素", "实际说明"], [["使用角色", role], ["功能入口", entry], ["指标或状态", visible], ["判读范围", scope]]
    if profile in {"data_entry", "configuration"}:
        return ["维护要素", "实际说明"], [["操作角色", role], ["功能入口", entry], ["输入或参数", joined(capability.get("inputs"), visible)], ["规则约束", joined(capability.get("business_rules"), scope)]]
    if profile == "file_processing":
        return ["处理要素", "实际说明"], [["操作角色", role], ["处理入口", entry], ["文件与参数", visible], ["输出范围", joined(capability.get("outputs"), scope)]]
    return ["操作要素", "实际说明"], [["适用角色", role], ["进入位置", entry], ["可见内容", visible], ["业务范围", scope]]


def result_rows(capability: dict[str, Any], profile: str) -> list[list[str]]:
    inputs = joined(capability.get("inputs"), capability.get("visible_elements", ""))
    outputs = joined(capability.get("outputs"), capability["success_feedback"])
    rules = joined(capability.get("business_rules") or capability.get("restrictions"))
    states = joined(capability.get("state_changes"), capability["success_feedback"])
    fields = joined(capability.get("result_fields"), outputs)
    scope = str(capability.get("data_scope") or joined(capability.get("restrictions")))
    if profile == "query":
        return [["筛选或定位条件", inputs], ["结果字段", fields], ["数据范围", scope], ["核对结论", outputs]]
    if profile == "approval":
        return [["核验内容", inputs], ["处理规则", rules], ["状态变化", states], ["完成反馈", outputs]]
    if profile in {"analysis", "monitoring"}:
        return [["输入与范围", inputs], ["观察字段", fields], ["判读规则", rules], ["结果或状态", outputs]]
    if profile == "configuration":
        return [["配置内容", inputs], ["取值或关联规则", rules], ["生效范围", scope], ["生效反馈", states]]
    if profile == "data_entry":
        return [["录入内容", inputs], ["字段或关联规则", rules], ["保存结果", outputs], ["状态反馈", states]]
    if profile == "file_processing":
        return [["文件与参数", inputs], ["处理规则", rules], ["输出内容", outputs], ["结果字段", fields]]
    return [["操作内容", inputs], ["业务限制", rules], ["可见结果", outputs], ["完成状态", states]]


def exception_rows(capability: dict[str, Any]) -> list[list[str]]:
    cases = capability.get("error_cases") or []
    rows = []
    for item in cases:
        condition = str(item.get("condition", "")).strip()
        resolution = str(item.get("resolution") or item.get("feedback") or "").strip()
        if not resolution:
            resolution = f"根据“{capability['name']}”页面提示修正输入或状态后重试。"
        rows.append([condition, resolution])
    if not rows:
        rows = [[str(capability.get("error_feedback", "页面提示操作未完成")),
                 f"返回“{capability['name']}”核对当前对象、输入和状态，修正后重新执行。"]]
    return rows


def capability_blocks(capability: dict[str, Any], business: dict[str, Any], index: int) -> tuple[str, list[dict[str, Any]], str]:
    """Return a complete capability description; kept as a reusable/tested contract."""
    profile = operation_profile(capability)
    copy = PROFILE_COPY[profile]
    headers, rows = table_rows(capability, business, profile)
    role = capability.get("actor") or business["target_users"]
    blocks: list[dict[str, Any]] = [
        {"type": "paragraph", "text": f"{evidence_purpose(capability, str(role))} {role}通过“{capability['entry']}”进入，页面可见{capability['visible_elements']}。"},
        {"type": "table", "headers": headers, "rows": rows},
        {"type": "facts", "items": [
            {"label": "执行角色", "value": role}, {"label": "功能入口", "value": capability["entry"]},
            {"label": "完成标志", "value": capability["success_feedback"]},
        ]},
    ]
    prerequisites = effective_prerequisites(capability)
    if prerequisites:
        blocks += [{"type": "subheading", "text": "使用条件"}, {"type": "bullets", "items": prerequisites}]
    blocks += [
        {"type": "steps", "items": capability["steps"]},
        {"type": "table", "headers": ["核对项目", "实际要求"], "rows": result_rows(capability, profile)},
        {"type": "table", "headers": ["异常现象", "处理方法"], "rows": exception_rows(capability)},
    ]
    return copy["chapter_lead"], blocks, profile


def block_text(block: dict[str, Any]) -> str:
    if block.get("type") in {"paragraph", "note", "subheading"}:
        return str(block.get("text", ""))
    if block.get("type") in {"steps", "bullets"}:
        return "。".join(text_list(block.get("items")))
    if block.get("type") == "table":
        return "。".join("：".join(map(str, row)) for row in block.get("rows", []))
    if block.get("type") == "facts":
        return "。".join(f"{item.get('label', '')}：{item.get('value', '')}" for item in block.get("items", []))
    return ""


def content_quality(capability_pages: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    sentences: list[str] = []
    signatures: list[str] = []
    minimum_chars = int(rules.get("minimum_capability_characters", 180))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, page in enumerate(capability_pages):
        grouped[str(page.get("capability_key") or f"section-{index:03d}")].append(page)
        signature_parts = [str(page.get("operation_profile", "")), str(page.get("subpage_kind", ""))]
        for block in page.get("blocks", []):
            signature_parts.append(":".join([str(block.get("type", "")), str(block.get("title", "")), "/".join(map(str, block.get("headers", []))) ]))
        signatures.append("|".join(signature_parts))
    for capability_key, group in grouped.items():
        text = "。".join(page.get("lead", "") + "。" + "。".join(block_text(block) for block in page.get("blocks", [])) for page in group)
        characters = len(re.sub(r"\s+", "", text))
        if characters < minimum_chars:
            issues.append({"code": "capability_content_thin", "title": group[0]["title"], "actual": characters, "minimum": minimum_chars})
        steps = [item for page in group for block in page.get("blocks", []) if block.get("type") == "steps" for item in block.get("items", [])]
        if len(steps) < int(rules.get("minimum_steps_per_capability", 2)):
            issues.append({"code": "capability_steps_too_few", "title": group[0]["title"]})
        detail_count = sum(bool(block_text(block).strip()) for page in group for block in page.get("blocks", []) if block.get("type") != "image")
        if detail_count < int(rules.get("minimum_detail_items", 1)):
            issues.append({"code": "capability_business_detail_missing", "title": group[0]["title"]})
        sentences.extend(item.strip() for item in re.split(r"[。！？；]", text) if len(item.strip()) >= int(rules.get("minimum_sentence_characters_for_repeat_check", 12)))
    counts = Counter(sentences)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    ratio = repeated / max(1, len(sentences))
    if ratio > float(rules.get("maximum_repeated_sentence_ratio", 0.2)):
        issues.append({"code": "repeated_sentence_ratio_high", "actual": round(ratio, 4), "maximum": rules.get("maximum_repeated_sentence_ratio")})
    signature_counts = Counter(signatures)
    if signature_counts and max(signature_counts.values()) > int(rules.get("maximum_identical_block_signature_count", 2)):
        issues.append({"code": "identical_section_structure_repeated", "actual": max(signature_counts.values()), "maximum": rules.get("maximum_identical_block_signature_count")})
    return {"status": "pass" if not issues else "fail", "issues": issues,
            "metrics": {"capabilities": len(grouped), "capability_sections": len(capability_pages),
                        "sentence_count": len(sentences), "repeated_sentence_ratio": round(ratio, 4),
                        "block_signatures": dict(signature_counts)}}


def image_blocks(capability: dict[str, Any], shots: dict[str, dict[str, Any]], figure: int,
                 allow_placeholders: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    blocks: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    shot_ids = capability.get("screenshot_ids") or []
    for shot_id in shot_ids:
        shot = shots.get(shot_id)
        if shot and shot.get("status") == "pass" and Path(shot["path"]).is_file():
            blocks.append({"type": "image", "path": shot["path"], "width_mm": 150,
                           "caption": f"图 {figure}  {shot.get('title', capability['name'])}"})
            figure += 1
        else:
            missing.append({"capability": capability["name"], "screenshot_id": shot_id})
    if not shot_ids:
        missing.append({"capability": capability["name"], "screenshot_id": "未分配"})
    if missing and allow_placeholders:
        blocks.append({"type": "placeholder_image", "caption": f"截图预留：{capability['name']}"})
    return blocks, missing, figure


def capability_pages(capability: dict[str, Any], business: dict[str, Any], chapter: int,
                     shots: dict[str, dict[str, Any]], figure: int, allow_placeholders: bool
                     ) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    _, all_blocks, profile = capability_blocks(capability, business, chapter)
    copy = PROFILE_COPY[profile]
    title_overrides = capability.get("manual_titles") or {}
    procedure_title = str(title_overrides.get("procedure") or copy["procedure_title"])
    detail_title = str(title_overrides.get("detail") or copy["detail_title"])
    exception_title = str(title_overrides.get("exception") or "异常情形与复核方法")
    key = str(capability.get("id") or f"CAP-{chapter:03d}")
    evidence = capability["evidence_ids"]
    role = capability.get("actor") or business["target_users"]
    pictures, missing, figure = image_blocks(capability, shots, figure, allow_placeholders)
    headers, rows = table_rows(capability, business, profile)
    prerequisites = effective_prerequisites(capability)
    variant = max(0, chapter - 4) % 5
    inputs = text_list(capability.get("inputs"))
    result_fields = text_list(capability.get("result_fields"))
    focus_items = inputs if profile in {"data_entry", "configuration", "file_processing"} else result_fields
    facts_block = {"type": "facts", "items": [
        {"label": "使用角色", "value": role},
        {"label": "功能入口", "value": capability["entry"]},
        {"label": "完成标志", "value": capability["success_feedback"]},
    ]}
    table_block = {"type": "table", "headers": headers, "rows": rows}
    focus_blocks = ([{"type": "subheading", "text": "录入与核对范围" if inputs else "页面核对重点"},
                     {"type": "bullets", "items": focus_items}] if focus_items else [])
    prerequisite_note = ({"type": "note", "title": "本次操作条件",
                          "text": joined(prerequisites)} if prerequisites else None)
    prerequisite_blocks = ([{"type": "subheading", "text": "进入页面前的条件"},
                            {"type": "bullets", "items": prerequisites}] if prerequisites else [])

    overview_blocks: list[dict[str, Any]] = [
        {"type": "paragraph", "text": evidence_purpose(capability, str(role))},
        {"type": "paragraph", "text": f"{role}从“{capability['entry']}”进入后，可直接看到{capability['visible_elements']}。"},
    ]
    if variant == 0:
        overview_blocks += [facts_block, table_block] + prerequisite_blocks
    elif variant == 1:
        overview_blocks += [table_block] + focus_blocks + ([prerequisite_note] if prerequisite_note else [])
    elif variant == 2:
        overview_blocks += focus_blocks + [facts_block] + ([prerequisite_note] if prerequisite_note else [])
    elif variant == 3:
        overview_blocks += [table_block, {"type": "note", "title": "完成判断",
                                          "text": f"“{capability['name']}”以{capability['success_feedback']}作为本次完成标志。"}] + prerequisite_blocks
    else:
        overview_blocks += [facts_block, {"type": "note", "title": "数据范围",
                                          "text": str(capability.get("data_scope") or joined(capability.get("restrictions")))}] + focus_blocks + prerequisite_blocks

    steps = [block for block in all_blocks if block.get("type") == "steps"]
    result_header_variants = [
        ["核对项目", "实际要求"], ["复核内容", "页面依据"], ["业务要点", "判断口径"],
        ["完成检查", "可见结果"], ["结果项目", "确认方法"],
    ]
    exception_header_variants = [
        ["异常现象", "处理方法"], ["未完成情形", "修正方式"], ["问题表现", "复核动作"],
        ["受阻原因", "处理建议"], ["页面反馈", "再次操作前的检查"],
    ]
    result_table = {"type": "table", "headers": result_header_variants[variant],
                    "rows": result_rows(capability, profile)}
    exception_table = {"type": "table", "headers": exception_header_variants[variant],
                       "rows": exception_rows(capability)}
    feedback_note = {"type": "note", "title": "操作结果",
                     "text": f"完成“{capability['name']}”后，{capability['success_feedback']}"}
    feedback_blocks = [{"type": "subheading", "text": "页面反馈与完成标志"},
                       {"type": "paragraph", "text": str(capability["success_feedback"])}]
    if variant == 0:
        procedure_core = steps + pictures + feedback_blocks
    elif variant == 1:
        procedure_core = pictures + steps + [feedback_note]
    elif variant == 2:
        procedure_core = steps + [{"type": "subheading", "text": "结果出现的位置"}, result_table] + pictures + [feedback_note]
    elif variant == 3:
        procedure_core = [feedback_note] + steps + pictures
    else:
        procedure_core = steps + pictures + [{"type": "facts", "items": [
            {"label": "完成结果", "value": capability["success_feedback"]},
            {"label": "失败提示", "value": capability.get("error_feedback", "以页面提示为准")},
        ]}]

    expanded_detail = profile in {"approval", "analysis", "monitoring"}
    if expanded_detail:
        detail_blocks: list[dict[str, Any]] = [result_table, exception_table,
            {"type": "note", "title": "复核闭环",
             "text": f"处理“{capability['name']}”的异常后，应重新执行关键步骤，并核对{joined(result_fields, capability['success_feedback'])}。"}]
        procedure_blocks = procedure_core
    else:
        if variant == 2:
            procedure_blocks = procedure_core + [exception_table]
        elif variant == 4:
            procedure_blocks = procedure_core + [exception_table, result_table]
        else:
            procedure_blocks = procedure_core + [result_table, exception_table]
        detail_blocks = []

    context_lead = (f"本章说明{capability['name']}的业务对象、页面入口和完成结果，"
                    f"适用角色为{role}。")
    procedure_lead = (f"从“{capability['entry']}”开始，按页面顺序完成{joined(capability.get('steps'))}，"
                      f"并以界面反馈确认处理结果。")
    combined_blocks = overview_blocks + [
        {"type": "subheading", "text": procedure_title},
        {"type": "paragraph", "text": procedure_lead},
    ] + procedure_blocks
    pages = [
        {"kind": "section", "title": f"{chapter} {capability['name']}", "level": 1,
         "lead": context_lead, "operation_profile": profile, "subpage_kind": "combined",
         "capability_key": key, "evidence_ids": evidence, "blocks": combined_blocks},
    ]
    if expanded_detail:
        pages.append({"kind": "section", "title": f"{chapter}.1 {detail_title}与{exception_title}", "level": 2,
                      "lead": f"完成{capability['name']}后重点核对{joined(result_fields, capability['success_feedback'])}，"
                              f"异常反馈为{capability.get('error_feedback', '以页面提示为准')}。",
                      "operation_profile": profile, "subpage_kind": "detail",
                      "capability_key": key, "evidence_ids": evidence, "blocks": detail_blocks})
    return pages, missing, figure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--quality-rules", type=Path, default=DEFAULT_QUALITY_RULES)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-placeholders", action="store_true")
    args = parser.parse_args()
    facts, business = load_json(args.facts), load_json(args.business)
    index = load_json(args.screenshots) if args.screenshots and args.screenshots.exists() else {"captures": []}
    shots = screenshot_map(index)
    slots = find_slots({"facts": facts, "business": business})
    if slots:
        print(f"PENDING_SLOTS={len(slots)}")
        return 2
    capabilities = business.get("capabilities", [])
    if not capabilities:
        print("PENDING_CAPABILITIES=1")
        return 2
    manual_facts = dict(facts)
    if isinstance(manual_facts.get("rightsholder"), dict):
        manual_facts["rightsholder"] = manual_facts["rightsholder"].get("name", "")

    capability_index = [[str(index), item["name"], str(item.get("actor") or business["target_users"]), str(item["entry"])]
                        for index, item in enumerate(capabilities, 1)]
    profile_groups: dict[str, list[str]] = defaultdict(list)
    for item in capabilities:
        profile_groups[operation_profile(item)].append(item["name"])
    composition_rows = [[PROFILE_COPY[key]["detail_title"], "、".join(names)] for key, names in profile_groups.items()]

    pages: list[dict[str, Any]] = [
        {"kind": "cover", "title": facts["software_full_name"], "lead": f"操作手册 · {facts['version']}"},
        {"kind": "toc", "title": "目录", "lead": "目录由 Word 自动生成，可点击条目跳转。"},
        {"kind": "section", "title": "1 软件概述", "level": 1, "lead": business["software_purpose"], "blocks": [
            {"type": "facts", "items": [
                {"label": "软件版本", "value": facts["version"]}, {"label": "目标用户", "value": business["target_users"]},
                {"label": "应用领域", "value": business["industry_domain"]}]},
            {"type": "subheading", "text": "建设目的"},
            {"type": "paragraph", "text": business["development_purpose"]},
            {"type": "subheading", "text": "主要功能"},
            {"type": "paragraph", "text": business["main_functions"]}]},
        {"kind": "section", "title": "1.1 功能组成与使用对象", "level": 2,
         "lead": "功能结构依据真实页面、路由、操作角色和项目证据整理。", "blocks": [
             {"type": "table", "headers": ["使用侧重点", "对应功能"], "rows": composition_rows,
              "widths_mm": [48, 112]}]},
        {"kind": "section", "title": "2 运行环境与使用准备", "level": 1,
         "lead": "首次使用前应确认运行平台、支撑环境、访问入口和账号权限均处于可用状态。", "blocks": [
             {"type": "table", "headers": ["环境项目", "实际要求"], "rows": [
                 ["运行平台", business["runtime_platform"]], ["支撑环境", business["runtime_support"]],
                 ["启动或访问方式", business.get("startup", "按实际部署入口启动或访问软件")]],
              "widths_mm": [42, 118]},
             {"type": "subheading", "text": "界面与操作约定"},
             {"type": "paragraph", "text": business.get("interface_structure") or "进入系统后按照菜单、列表、详情和操作反馈完成业务处理。"}]},
        {"kind": "section", "title": "3 功能入口与总体流程", "level": 1,
         "lead": business.get("workflow_summary", business["main_functions"]), "blocks": [
             {"type": "table", "headers": ["序号", "功能", "主要角色", "入口"], "rows": capability_index,
              "widths_mm": [14, 55, 35, 56]}]},
    ]

    missing_shots: list[dict[str, str]] = []
    capability_page_list: list[dict[str, Any]] = []
    chapter, figure = 4, 1
    for capability in capabilities:
        built, missing, figure = capability_pages(capability, business, chapter, shots, figure, args.allow_placeholders)
        pages.extend(built)
        capability_page_list.extend(built)
        missing_shots.extend(missing)
        chapter += 1
    if business.get("faq"):
        pages.append({"kind": "section", "title": f"{chapter} 常见问题", "level": 1,
                      "lead": "以下问题对应实际使用中的判断点和处理路径。", "blocks": [
                          {"type": "table", "headers": ["问题", "处理方法"],
                           "rows": [[item.get("question", ""), item.get("answer", "")] for item in business["faq"]],
                           "widths_mm": [55, 105]}]})
        chapter += 1
    if business.get("terms"):
        pages.append({"kind": "section", "title": f"{chapter} 术语说明", "level": 1,
                      "lead": "术语采用项目界面、业务状态和源码中能够核验的名称。", "blocks": [
                          {"type": "table", "headers": ["术语", "说明"],
                           "rows": [[item.get("term", ""), item.get("description", "")] for item in business["terms"]],
                           "widths_mm": [45, 115]}]})

    quality = content_quality(capability_page_list, load_json(args.quality_rules.resolve()))
    preferred_min, preferred_max = 40, 60
    logical_pages = len(pages)
    length_status = "within_preferred_range" if preferred_min <= logical_pages <= preferred_max else (
        "content_driven_below_preferred" if logical_pages < preferred_min else "review_above_preferred")
    payload = {
        "facts": manual_facts,
        "document": {"title": "操作手册", "type": "软件著作权鉴别材料", "edition": "正式版",
                     "status": "待渲染复核", "date": now_iso()[:10]},
        "pages": pages, "content_quality": quality,
        "generation": {"content_based": True, "layout_strategy": "adaptive_by_operation_profile",
                       "logical_sections": len(pages), "preferred_logical_page_range": [preferred_min, preferred_max],
                       "length_status": length_status, "screenshot_mode": index.get("mode"),
                       "screenshot_state": index.get("state"), "missing_screenshots": missing_shots},
    }
    if missing_shots and not args.allow_placeholders:
        print(f"MISSING_SCREENSHOTS={len(missing_shots)}")
        return 3
    save_json(args.output.resolve(), payload)
    print(f"MANUAL_CONTENT={args.output.resolve()}")
    print(f"SECTIONS={len(pages)} CAPABILITIES={len(capabilities)} SCREENSHOTS={figure - 1} MISSING={len(missing_shots)} QUALITY={quality['status']}")
    return 0 if quality["status"] == "pass" else 4


if __name__ == "__main__":
    raise SystemExit(main())
