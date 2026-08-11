#!/usr/bin/env python3
"""Convert a page storyboard into an editable evidence-led manual skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import load_json, now_iso, save_json


def page_blocks(item: dict) -> list[dict]:
    kind = item.get("kind")
    if kind in {"cover", "toc"}:
        return []
    blocks: list[dict] = []
    if item.get("status") == "evidence_debt":
        blocks.append({
            "type": "note",
            "title": "证据任务",
            "text": "本页需由申请人结合运行界面、项目代码或实际结果补充不同的事实；完成后移除本提示。"
        })
    if kind == "feature":
        if "完成任务" in item.get("title", ""):
            blocks.extend([
                {"type": "table", "headers": ["任务要素", "经确认内容"], "rows": [
                    ["适用角色", "【待申请人确认：角色】"],
                    ["入口路径", "【待申请人确认：真实菜单或路由】"],
                    ["前置条件", "【待申请人确认：数据与权限条件】"]
                ]},
                {"type": "steps", "items": [
                    "【待申请人确认：步骤一动作及界面反馈】",
                    "【待申请人确认：步骤二动作及界面反馈】",
                    "【待申请人确认：步骤三动作及界面反馈】"
                ]},
                {"type": "placeholder_image", "caption": "图【待编号】  【待申请人确认：真实界面与状态】"}
            ])
        else:
            blocks.extend([
                {"type": "table", "headers": ["核验项", "成功判据", "操作说明"], "rows": [
                    ["界面反馈", "【待申请人确认】", "【待申请人确认：界面状态】"],
                    ["数据变化", "【待申请人确认】", "【待申请人确认：结果数据】"],
                    ["异常路径", "【待申请人确认】", "【待申请人确认：异常提示】"]
                ]},
                {"type": "note", "title": "逻辑复核", "text": "只保留项目中可复现的成功结果和异常反馈。"}
            ])
    elif kind == "navigation":
        blocks.append({"type": "table", "headers": ["阅读入口", "对应任务", "章节"], "rows": [
            ["按角色", "【待申请人确认】", "【待生成】"],
            ["按工作流", "【待申请人确认】", "【待生成】"],
            ["按功能", "【待申请人确认】", "【待生成】"]
        ]})
    else:
        blocks.extend([
            {"type": "paragraph", "text": "【待申请人基于证据撰写：本页的独立事实与操作结论】"},
            {"type": "note", "title": "页面任务", "text": item.get("intent", "补充经确认内容。")}
        ])
    return blocks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    facts = load_json(args.facts)
    plan = load_json(args.plan)
    pages: list[dict] = []
    for item in plan.get("pages", []):
        page = {
            "kind": item.get("kind") if item.get("kind") in {"cover", "toc", "section"} else "content",
            "title": facts.get("software_full_name") if item.get("kind") == "cover" else item.get("title"),
            "level": 1 if item.get("kind") in {"overview", "architecture", "workflow", "revision", "evidence"} else 2,
            "eyebrow": f"PAGE PLAN {item.get('page_no', 0):02d}  ·  {str(item.get('kind', '')).upper()}",
            "lead": item.get("intent", ""),
            "evidence_ids": item.get("evidence_ids", []),
            "blocks": page_blocks(item),
            "plan_page_no": item.get("page_no"),
            "evidence_status": item.get("status")
        }
        pages.append(page)
    payload = {
        "facts": facts,
        "document": {
            "title": "操作手册",
            "type": "软件著作权鉴别材料",
            "edition": "完整成册版",
            "status": "页级证据骨架",
            "date": now_iso()[:10],
            "target_pages": plan.get("target_pages", len(pages)),
            "storyboard": str(args.plan.resolve())
        },
        "pages": pages
    }
    save_json(args.output.resolve(), payload)
    print(f"MANUAL_SEED={args.output.resolve()}")
    print(f"LOGICAL_PAGES={len(pages)} EVIDENCE_DEBT={len(plan.get('evidence_debt_pages', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
