#!/usr/bin/env python3
"""Build a plain-language manual model from confirmed business facts and screenshots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json
from product_model import find_slots


def screenshot_map(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item.get("id"): item for item in index.get("captures", []) if item.get("id") and item.get("path")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-placeholders", action="store_true")
    args = parser.parse_args()
    facts = load_json(args.facts)
    business = load_json(args.business)
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
    pages: list[dict[str, Any]] = [
        {"kind": "cover", "title": facts["software_full_name"],
         "lead": f"操作手册 · {facts['version']}"},
        {"kind": "toc", "title": "目录", "lead": "目录由 Word 自动生成，可点击条目跳转。"},
        {"kind": "section", "title": "1 软件概述", "level": 1,
         "lead": business["software_purpose"], "blocks": [
             {"type": "table", "headers": ["项目", "说明"], "rows": [
                 ["开发目的", business["development_purpose"]],
                 ["目标用户", business["target_users"]],
                 ["面向领域", business["industry_domain"]],
                 ["软件版本", facts["version"]],
             ]},
             {"type": "paragraph", "text": business["main_functions"]},
         ]},
        {"kind": "section", "title": "2 运行环境与启动", "level": 1,
         "lead": "说明使用软件前需要具备的环境和启动方式。", "blocks": [
             {"type": "table", "headers": ["项目", "说明"], "rows": [
                 ["运行平台", business["runtime_platform"]],
                 ["支撑环境", business["runtime_support"]],
                 ["启动方式", business.get("startup", "按部署说明启动软件")],
             ]},
             {"type": "paragraph", "text": business.get("interface_structure", "")},
         ]},
        {"kind": "section", "title": "3 主要操作流程", "level": 1,
         "lead": business.get("workflow_summary", business["main_functions"]), "blocks": []},
    ]
    missing_shots: list[dict[str, str]] = []
    chapter = 4
    figure = 1
    for capability in capabilities:
        blocks: list[dict[str, Any]] = [
            {"type": "paragraph", "text": capability["purpose"]},
            {"type": "table", "headers": ["操作要素", "说明"], "rows": [
                ["适用用户", capability.get("actor", business["target_users"])],
                ["进入位置", capability["entry"]],
                ["页面内容", capability["visible_elements"]],
                ["使用限制", "；".join(capability.get("restrictions", [])) or "按照页面提示和业务状态操作"],
            ]},
            {"type": "steps", "items": capability["steps"]},
            {"type": "note", "title": "操作结果", "text": capability["success_feedback"]},
            {"type": "note", "title": "异常与输入提示", "text": capability["error_feedback"]},
        ]
        for shot_id in capability.get("screenshot_ids", []):
            shot = shots.get(shot_id)
            if shot and shot.get("status") == "pass" and Path(shot["path"]).is_file():
                blocks.append({"type": "image", "path": shot["path"], "width_mm": 160,
                               "caption": f"图 {figure}  {shot.get('title', capability['name'])}"})
                figure += 1
            else:
                missing_shots.append({"capability": capability["name"], "screenshot_id": shot_id})
        if not capability.get("screenshot_ids"):
            missing_shots.append({"capability": capability["name"], "screenshot_id": "未分配"})
        if any(item["capability"] == capability["name"] for item in missing_shots) and args.allow_placeholders:
            blocks.append({"type": "placeholder_image", "caption": f"截图预留：{capability['name']}"})
        pages.append({
            "kind": "section", "title": f"{chapter} {capability['name']}", "level": 1,
            "lead": capability["purpose"], "evidence_ids": capability["evidence_ids"], "blocks": blocks,
        })
        chapter += 1
    if business.get("faq"):
        pages.append({"kind": "section", "title": f"{chapter} 常见问题", "level": 1,
                      "lead": "以下问题来自实际操作中的常见判断点。", "blocks": [
                          {"type": "table", "headers": ["问题", "处理方法"],
                           "rows": [[item.get("question", ""), item.get("answer", "")] for item in business["faq"]]}
                      ]})
        chapter += 1
    if business.get("terms"):
        pages.append({"kind": "section", "title": f"{chapter} 术语说明", "level": 1,
                      "lead": "统一手册中的界面和状态名称。", "blocks": [
                          {"type": "table", "headers": ["术语", "说明"],
                           "rows": [[item.get("term", ""), item.get("description", "")] for item in business["terms"]]}
                      ]})
    payload = {
        "facts": manual_facts,
        "document": {"title": "操作手册", "type": "软件著作权鉴别材料",
                     "edition": "正式版", "status": "待渲染复核", "date": now_iso()[:10]},
        "pages": pages,
        "generation": {"content_based": True, "logical_sections": len(pages),
                       "screenshot_mode": index.get("mode"), "missing_screenshots": missing_shots},
    }
    if missing_shots and not args.allow_placeholders:
        print(f"MISSING_SCREENSHOTS={len(missing_shots)}")
        return 3
    save_json(args.output.resolve(), payload)
    print(f"MANUAL_CONTENT={args.output.resolve()}")
    print(f"SECTIONS={len(pages)} CAPABILITIES={len(capabilities)} SCREENSHOTS={figure - 1} MISSING={len(missing_shots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
