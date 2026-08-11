#!/usr/bin/env python3
"""Create a content-sized manual storyboard without padding to a fixed page target."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import find_slots, load_json, now_iso, save_json, sha256_text

FRONT = [
    ("cover", "封面", "标明软件名称、版本、材料类型与著作权人"),
    ("toc", "目录", "由 Word TOC 域生成可跳转目录"),
    ("overview", "软件概述", "说明开发目的、目标用户、使用范围和核心价值"),
    ("environment", "运行环境与启动方式", "说明经证据确认的运行条件和访问方式"),
    ("navigation", "界面结构与操作导航", "按真实页面、角色和任务组织阅读入口"),
    ("workflow", "主要操作流程", "概括核心任务的先后关系和结果状态"),
]
CLOSING = [
    ("exception", "输入校验与异常提示", "仅记录项目中真实存在的限制和反馈"),
    ("faq", "常见问题", "基于实际易错步骤提供判断方法"),
    ("glossary", "必要术语说明", "统一真实界面、按钮和状态名称"),
    ("revision", "版本与修订记录", "记录材料版本和复核状态"),
]


def make_page(no: int, kind: str, title: str, intent: str, evidence_ids: list[str] | None = None,
              status: str = "evidence_debt") -> dict:
    return {
        "page_no": no, "kind": kind, "title": title, "intent": intent,
        "evidence_ids": evidence_ids or [], "status": status,
        "review": {"logical": False, "visual": False, "evidence": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-pages", default="auto",
                        help="auto uses only evidenced content; an integer is an advanced maximum, not a padding target")
    args = parser.parse_args()
    facts = load_json(args.facts)
    graph = load_json(args.evidence)
    candidates = [node for node in graph.get("nodes", []) if node.get("type") in {"capability", "capability_candidate"}]
    candidates.sort(key=lambda node: (-len(node.get("evidence_ids", [])), node.get("name", "")))
    explicit_max = None
    if str(args.target_pages).lower() != "auto":
        try:
            explicit_max = int(args.target_pages)
        except ValueError:
            parser.error("--target-pages must be auto or an integer from 6 to 120")
        if not 6 <= explicit_max <= 120:
            parser.error("--target-pages must be within 6..120")

    pages: list[dict] = []
    facts_ready = not find_slots(facts)
    for kind, title, intent in FRONT:
        status = "ready" if kind in {"cover", "toc"} and facts_ready else "evidence_debt"
        pages.append(make_page(len(pages) + 1, kind, title, intent, status=status))

    used = 0
    for candidate in candidates:
        if explicit_max and len(pages) + len(CLOSING) >= explicit_max:
            break
        evidence_ids = list(dict.fromkeys([candidate.get("id", ""), *candidate.get("evidence_ids", [])]))
        evidence_ids = [value for value in evidence_ids if value]
        confirmed = candidate.get("status") in {"human_confirmed", "runtime_confirmed", "published"}
        status = "ready" if confirmed and evidence_ids else "evidence_debt"
        name = candidate.get("name", candidate.get("id", "功能"))
        pages.append(make_page(
            len(pages) + 1, "feature", f"{used + 1}. {name}",
            "说明用途、入口、界面、操作、限制、成功反馈、异常反馈和对应截图",
            evidence_ids, status,
        ))
        used += 1
        has_rich_evidence = len(evidence_ids) >= 3 or candidate.get("complexity") == "high"
        if has_rich_evidence and (not explicit_max or len(pages) + len(CLOSING) < explicit_max):
            pages.append(make_page(
                len(pages) + 1, "feature_result", f"{used}. {name}—结果核验与异常",
                "补充不同于操作步骤的结果、数据变化和真实异常路径", evidence_ids, status,
            ))

    route_ids = [node["id"] for node in graph.get("nodes", []) if node.get("type") == "route"]
    shot_ids = [node["id"] for node in graph.get("nodes", []) if node.get("type") == "screenshot"]
    for kind, title, intent in CLOSING:
        evidence_ids = route_ids[:5] if kind == "exception" else shot_ids[:5]
        if kind in {"faq", "glossary", "revision"}:
            status = "ready" if facts_ready and used else "evidence_debt"
        else:
            status = "ready" if evidence_ids else "evidence_debt"
        pages.append(make_page(len(pages) + 1, kind, title, intent, evidence_ids, status))

    debt = [item["page_no"] for item in pages if item["status"] == "evidence_debt"]
    result = {
        "schema_version": "2.0", "generated_at": now_iso(),
        "target_pages": "content_based" if explicit_max is None else explicit_max,
        "planned_pages": len(pages), "planning_mode": "content_based_no_padding",
        "facts_sha256": sha256_text(__import__("json").dumps(facts, ensure_ascii=False, sort_keys=True)),
        "facts_slots": find_slots(facts), "candidate_capabilities_used": used,
        "evidence_debt_pages": debt, "release_ready": not debt and facts_ready,
        "pages": pages,
        "note": "页数由真实功能、操作复杂度、异常路径和截图证据决定，不设置40页、60页或66页下限。",
    }
    save_json(args.output.resolve(), result)
    print(f"MANUAL_PLAN={args.output.resolve()}")
    print(f"PAGES={len(pages)} CAPABILITIES={used} EVIDENCE_DEBT={len(debt)} FACT_SLOTS={len(result['facts_slots'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
