#!/usr/bin/env python3
"""Create an evidence-scaled 40–66 page storyboard, extendable to 72 pages."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import find_slots, load_json, now_iso, save_json, sha256_text


FRONT = [
    ("cover", "封面", "建立软件名称、版本、材料类型与权利人身份"),
    ("identity", "文档身份与修订状态", "锁定文档版本、适用范围和修订记录"),
    ("toc", "智能目录（上）", "由 Word TOC 域生成章节跳转"),
    ("navigation", "角色与流程导航", "按角色和端到端流程提供第二种阅读入口"),
    ("overview", "软件定位与使用边界", "说明真实目标、对象和不覆盖范围"),
    ("architecture", "系统组成与信息流", "用项目结构解释模块关系"),
    ("environment", "运行环境与访问方式", "记录可核验环境、地址形态和客户端条件"),
    ("concept", "关键对象与状态", "解释贯穿操作过程的数据对象和状态"),
    ("role", "角色与职责", "说明真实角色及其任务范围"),
    ("permission", "权限矩阵", "将角色、入口、动作和数据范围对应"),
    ("access", "登录与身份核验", "形成登录动作、反馈与结果闭环"),
    ("workflow", "端到端工作流", "展示核心场景的先后关系和状态变化")
]

CLOSING = [
    ("query", "查询与筛选", "说明真实查询条件和结果核验"),
    ("report", "统计、报表与输出", "说明有证据支持的汇总或导出能力"),
    ("exception", "输入校验与错误反馈", "记录项目中真实存在的校验路径"),
    ("exception", "权限、状态与冲突处理", "解释权限不足或状态冲突的真实反馈"),
    ("maintenance", "配置与维护", "仅纳入项目中可验证的参数和管理能力"),
    ("security", "账号、数据与审计提示", "陈述可验证的账号和数据边界"),
    ("faq", "常见问题", "基于实际易错步骤整理问题与判断方法"),
    ("glossary", "界面术语", "统一菜单、按钮、状态和数据名称"),
    ("evidence", "证据索引", "映射功能、截图、路由和源文件"),
    ("revision", "版本与修订记录", "记录材料生成、复核和发布状态")
]


def page(no: int, kind: str, title: str, intent: str, evidence_ids: list[str] | None = None,
         status: str = "evidence_debt") -> dict:
    return {
        "page_no": no,
        "kind": kind,
        "title": title,
        "intent": intent,
        "evidence_ids": evidence_ids or [],
        "status": status,
        "content_owner": "applicant",
        "review": {"logical": False, "visual": False, "evidence": False}
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-pages", default="auto",
                        help="auto or an integer from 40 to 72")
    args = parser.parse_args()
    facts = load_json(args.facts)
    graph = load_json(args.evidence)

    candidates = [n for n in graph.get("nodes", []) if n.get("type") == "capability_candidate"]
    candidates.sort(key=lambda n: (-len(n.get("evidence_ids", [])), n.get("name", "")))
    if str(args.target_pages).lower() == "auto":
        target_pages = min(66, max(40, 22 + 2 * min(22, len(candidates))))
        planning_mode = "auto_by_evidence_density"
    else:
        try:
            target_pages = int(args.target_pages)
        except ValueError:
            parser.error("--target-pages must be auto or an integer from 40 to 72")
        if not 40 <= target_pages <= 72:
            parser.error("--target-pages must be within 40..72")
        planning_mode = "explicit"
    pages: list[dict] = []
    for kind, title, intent in FRONT:
        auto_ready = kind in {"cover", "identity", "toc"} and not find_slots(facts)
        pages.append(page(len(pages) + 1, kind, title, intent, status="ready" if auto_ready else "evidence_debt"))

    feature_page_count = target_pages - len(FRONT) - len(CLOSING)
    feature_index = 0
    for offset in range(feature_page_count):
        pair_index = offset // 2
        phase = "完成任务" if offset % 2 == 0 else "核验结果与异常"
        if pair_index < len(candidates):
            cap = candidates[pair_index]
            evidence_ids = list(cap.get("evidence_ids", [])) + [cap["id"]]
            status = "ready" if cap.get("status") == "human_confirmed" else "evidence_debt"
            title = f"{pair_index + 1}. {cap.get('name', cap['id'])}—{phase}"
            intent = "以入口、前置条件、动作和反馈完成任务闭环" if offset % 2 == 0 else "记录成功判据、数据变化和真实异常路径"
            feature_index = max(feature_index, pair_index + 1)
        else:
            title = f"待确认核心任务 {pair_index + 1}—{phase}"
            intent = "从运行系统和申请人访谈补充一个不同的核心任务证据"
            evidence_ids = []
            status = "evidence_debt"
        pages.append(page(len(pages) + 1, "feature", title, intent, evidence_ids, status))

    route_ids = [n["id"] for n in graph.get("nodes", []) if n.get("type") == "route"]
    shot_ids = [n["id"] for n in graph.get("nodes", []) if n.get("type") == "screenshot"]
    for kind, title, intent in CLOSING:
        evidence_ids = route_ids[:3] if kind in {"query", "exception", "security"} else shot_ids[:3]
        pages.append(page(len(pages) + 1, kind, title, intent, evidence_ids,
                          "ready" if evidence_ids else "evidence_debt"))

    debt = [p["page_no"] for p in pages if p["status"] == "evidence_debt"]
    facts_text = __import__("json").dumps(facts, ensure_ascii=False, sort_keys=True)
    result = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "target_pages": target_pages,
        "planned_pages": len(pages),
        "recommended_range": [40, 66],
        "planning_mode": planning_mode,
        "facts_sha256": sha256_text(facts_text),
        "facts_slots": find_slots(facts),
        "candidate_capabilities_used": feature_index,
        "evidence_debt_pages": debt,
        "release_ready": not debt and not find_slots(facts),
        "pages": pages
    }
    save_json(args.output.resolve(), result)
    print(f"MANUAL_PLAN={args.output.resolve()}")
    print(f"PAGES={len(pages)} EVIDENCE_DEBT={len(debt)} FACT_SLOTS={len(result['facts_slots'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
