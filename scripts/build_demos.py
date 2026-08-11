#!/usr/bin/env python3
"""Build three fully sanitized product demos with real DOCX/PDF artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from common import load_json, save_json
from product_model import write_sha256s


SKILL_ROOT = Path(__file__).resolve().parents[1]

DEMOS = {
    "web-project": {
        "name": "脱敏工单协同管理系统", "kind": "Web管理系统", "extension": ".js", "file": "src/app.js",
        "config": ("package.json", {"name": "sanitized-ticket-console", "version": "1.0.0", "dependencies": {"express": "0.0.0"}, "scripts": {"start": "node src/app.js"}}),
        "entry": "浏览器首页的‘工单列表’菜单", "visible": "筛选区、工单表格、状态列和详情按钮",
        "purpose": "帮助内部服务人员查看、筛选并处理脱敏工单记录。",
        "steps": ["打开工单列表并等待表格加载", "输入脱敏关键字并执行查询", "打开目标工单详情", "更新处理状态并保存"],
        "success": "页面显示保存成功提示，状态列同步为新的处理状态。",
        "error": "必填内容为空时页面在对应输入框旁显示校验提示，保存操作不会提交。",
    },
    "desktop-project": {
        "name": "脱敏文件批次校验工具", "kind": "桌面客户端", "extension": ".cs", "file": "src/Program.cs",
        "config": ("SanitizedValidator.csproj", {"xml": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><OutputType>WinExe</OutputType><TargetFramework>net8.0</TargetFramework><AssemblyName>SanitizedValidator</AssemblyName><Version>1.0.0</Version></PropertyGroup></Project>"}),
        "entry": "客户端主窗口的‘选择目录’按钮", "visible": "目录输入框、校验规则区、进度列表和结果摘要",
        "purpose": "帮助工作人员对选定目录中的脱敏文件执行格式和命名校验。",
        "steps": ["启动客户端并打开主窗口", "选择包含脱敏文件的目录", "勾选需要执行的校验规则", "点击开始校验并等待完成"],
        "success": "进度列表逐项完成，结果区显示通过数量与需处理数量。",
        "error": "目录不存在或没有读取权限时，窗口显示原因并保持开始按钮可再次操作。",
    },
    "cli-project": {
        "name": "脱敏数据摘要命令行工具", "kind": "命令行工具", "extension": ".py", "file": "src/summary_cli.py",
        "config": ("pyproject.toml", {"text": "[project]\nname = \"sanitized-summary-cli\"\nversion = \"1.0.0\"\ndependencies = []\n"}),
        "entry": "终端中的 summary-cli 命令", "visible": "命令参数帮助、处理进度和结构化摘要结果",
        "purpose": "帮助开发和运维人员对脱敏文本数据生成数量、长度和分组摘要。",
        "steps": ["在终端查看命令帮助", "指定脱敏输入文件和输出目录", "执行摘要命令", "检查退出状态和生成的摘要文件"],
        "success": "命令返回退出状态0，并在目标目录生成摘要JSON文件。",
        "error": "输入文件不存在或格式错误时，命令输出明确错误原因并返回非0退出状态。",
    },
}


def source_lines(kind: str, count: int = 220) -> str:
    if kind.endswith(".py"):
        head = ["from pathlib import Path", "import json", "", "def summarize_record(record):", "    return {'length': len(record), 'empty': not bool(record.strip())}", ""]
        body = [f"def rule_{i}(value):\n    return summarize_record(value).get('length', 0) >= {i % 7}" for i in range(1, 72)]
    elif kind.endswith(".cs"):
        head = ["using System;", "using System.Collections.Generic;", "namespace SanitizedValidator {", "public static class Program {", "public static void Main() { Console.WriteLine(\"Ready\"); }"]
        body = [f"public static bool Rule{i}(string value) => value != null && value.Length >= {i % 7};" for i in range(1, 216)] + ["}", "}"]
    else:
        head = ["'use strict';", "function normalize(value) { return String(value || '').trim(); }"]
        body = [f"function validateRule{i}(value) {{ return normalize(value).length >= {i % 7}; }}" for i in range(1, 219)]
    return "\n".join(head + body) + "\n"


def draw_screenshot(path: Path, title: str, kind: str) -> None:
    image = Image.new("RGB", (1440, 900), "#f4f4f5")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1440, 72), fill="#222222")
    draw.text((40, 24), title, fill="white")
    draw.rectangle((32, 104, 260, 860), fill="#dedede", outline="#555555", width=2)
    draw.text((64, 150), kind, fill="#111111")
    for index in range(8):
        y = 210 + index * 62
        draw.rectangle((60, y, 232, y + 38), fill="#ffffff", outline="#777777")
        draw.text((76, y + 12), f"Module {index + 1}", fill="#222222")
    draw.rectangle((292, 104, 1408, 860), fill="#ffffff", outline="#555555", width=2)
    draw.text((332, 142), "Sanitized demonstration data", fill="#111111")
    for row in range(9):
        y = 210 + row * 60
        fill = "#eeeeee" if row % 2 == 0 else "#fafafa"
        draw.rectangle((332, y, 1366, y + 46), fill=fill, outline="#bbbbbb")
        draw.text((352, y + 14), f"DEMO-{row + 1:03d}    Verified record    Status: PASS", fill="#222222")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def sanitize_demo_text(demo: Path, project: Path) -> None:
    replacements = {
        str(project.resolve()): "${DEMO_PROJECT_ROOT}",
        str(SKILL_ROOT.resolve()): "${SKILL_ROOT}",
        str(Path.home().resolve()): "${HOME}",
        str(Path(tempfile.gettempdir()).resolve()): "${TEMP}",
    }
    if os.environ.get("SOFFICE"):
        replacements[str(Path(os.environ["SOFFICE"]).resolve().parent)] = "${OFFICE_RUNTIME}"
        replacements[str(Path(os.environ["SOFFICE"]).resolve())] = "${OFFICE_RUNTIME}/soffice.exe"
    for path in demo.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        updated = text
        for original, replacement in sorted(replacements.items(), key=lambda item: -len(item[0])):
            updated = (updated.replace(original, replacement)
                       .replace(original.replace("\\", "\\\\"), replacement)
                       .replace(original.replace("\\", "/"), replacement))
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def build_demo(root: Path, key: str, model: dict) -> None:
    demo, project = root / key, root / key / "project"
    if demo.exists():
        shutil.rmtree(demo)
    project.mkdir(parents=True)
    (project / "README.md").write_text(f"# {model['name']}\n\n这是完全脱敏的{model['kind']}演示项目。{model['purpose']}\n", encoding="utf-8")
    source = project / model["file"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(source_lines(model["extension"]), encoding="utf-8")
    config_name, config = model["config"]
    if "xml" in config:
        (project / config_name).write_text(config["xml"], encoding="utf-8")
    elif "text" in config:
        (project / config_name).write_text(config["text"], encoding="utf-8")
    else:
        save_json(project / config_name, config)
    completed = subprocess.run([sys.executable, str(SKILL_ROOT / "scripts/product_workflow.py"), "prepare", "--project", str(project)],
                               text=True, capture_output=True)
    if completed.returncode not in {0, 3}:
        raise RuntimeError(completed.stdout + completed.stderr)
    output = project / "软件著作权申请资料"
    intake = {
        "software_full_name": model["name"], "software_short_name": model["name"].replace("脱敏", "演示"), "version": "V1.0",
        "rightsholder": {"type": "legal_person", "name": "演示软件研究中心", "id_type": "演示标识",
                        "id_number": f"DEMO-{key.upper()}-ONLY"},
        "completion_date": "2026-08-01", "development_mode": "independent", "software_nature": "original",
        "publication": {"status": "unpublished"}, "rights_acquisition": "original",
        "rights_scope": {"type": "all", "detail": ""}, "ownership_notes": "完全合成的开源演示事实，不用于真实申请。",
        "screenshot_mode": "user_supplied", "confirmed_by": "demo-builder", "confirmed_at": "2026-08-11T00:00:00+08:00",
    }
    save_json(output / "一次性基础信息表.json", intake)
    main_functions = (f"软件围绕{model['purpose']}提供统一入口。使用者可从{model['entry']}进入，查看{model['visible']}，"
                      f"按照明确步骤完成输入、执行与结果核验。系统对必填项、输入路径和操作状态进行校验；成功时给出可见反馈，失败时保留原因并允许修正后重试。"
                      "操作记录使用脱敏标识，演示数据不包含真实个人信息。该演示只描述源码与截图中能够核验的能力，用于展示申请材料如何保持表单、手册、截图与代码一致。")
    business = {
        "software_classification": model["kind"], "software_purpose": model["purpose"],
        "target_users": "需要完成该项业务的内部工作人员与系统维护人员", "industry_domain": "通用软件工具",
        "development_purpose": model["purpose"], "development_environment": "Windows、macOS或Linux开发环境",
        "development_tools": "Python 3.10+及项目对应编译运行工具", "runtime_platform": model["kind"],
        "runtime_support": "具备文件读写权限的常规计算机环境", "main_functions": main_functions,
        "technical_features": "采用分层输入校验、确定性结果输出和明确错误状态，核心实现均可由源码行号追溯。",
        "startup": "按照项目README中的启动入口运行，看到主界面或命令帮助即表示启动成功。",
        "interface_structure": f"主要操作区域包括{model['visible']}。", "workflow_summary": "进入功能、提供输入、执行处理、查看结果、按提示修正异常。",
        "capabilities": [{"id": "CAP-core", "name": "核心处理", "purpose": model["purpose"], "actor": "内部工作人员",
                          "entry": model["entry"], "visible_elements": model["visible"], "steps": model["steps"],
                          "restrictions": ["只使用脱敏演示数据", "输入必须满足界面或命令提示"],
                          "success_feedback": model["success"], "error_feedback": model["error"],
                          "evidence_ids": ["FILE-core", "RUNTIME-core"], "screenshot_ids": ["user-shot-001"]}],
        "faq": [{"question": "操作失败后如何处理？", "answer": model["error"]}],
        "terms": [{"term": "脱敏数据", "description": "不包含可识别真实个人或组织的信息。"}],
        "confirmed_against_runtime": True, "generated_from_evidence": True,
    }
    save_json(output / ".工作区/business-understanding.json", business)
    draw_screenshot(output / "用户截图/01-core.png", model["name"], model["kind"])
    completed = subprocess.run([sys.executable, str(SKILL_ROOT / "scripts/product_workflow.py"), "generate", "--project", str(project)],
                               text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(f"{key} demo failed:\n{completed.stdout}\n{completed.stderr}")
    (demo / "README.md").write_text(f"""# {model['name']} Demo

完全脱敏、原创、可公开分发的 `{model['kind']}` 示例。`project/软件著作权申请资料` 包含一次性基础信息、项目分析、业务理解、截图、申请表、操作手册、代码材料、校验报告、哈希和最终正式资料。

演示证件号、名称、业务数据均为合成内容，不用于真实登记。
""", encoding="utf-8")
    sanitize_demo_text(demo, project)
    write_sha256s(output, output / "质量检查/SHA256SUMS.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SKILL_ROOT / "demos")
    parser.add_argument("--only", choices=sorted(DEMOS))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for key, model in DEMOS.items():
        if not args.only or args.only == key:
            print(f"BUILD_DEMO={key}", flush=True)
            build_demo(output, key, model)
    print(f"DEMOS={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
