from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RUN_DOCUMENT_E2E") == "1", "set RUN_DOCUMENT_E2E=1 with Word/LibreOffice available")
class ProductEndToEndTests(unittest.TestCase):
    def test_temporary_project_release(self):
        with tempfile.TemporaryDirectory(prefix="software-certificate-e2e-") as temp:
            project = Path(temp) / "project"
            source = project / "src" / "summary_cli.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "from pathlib import Path\n\n"
                + "\n".join(
                    f"def normalize_{index}(value):\n    return str(value).strip()[:{20 + index % 20}]"
                    for index in range(1, 90)
                )
                + "\n",
                encoding="utf-8",
            )
            (project / "README.md").write_text(
                "# 文本摘要命令行工具\n\n读取文本并输出规范化摘要。\n", encoding="utf-8"
            )

            prepare = subprocess.run(
                [sys.executable, str(ROOT / "scripts/product_workflow.py"), "prepare", "--project", str(project)],
                text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120,
            )
            self.assertIn(prepare.returncode, {0, 3}, prepare.stdout + prepare.stderr)

            output = project / "软件著作权申请资料"
            runtime_line = next(line for line in prepare.stdout.splitlines() if line.startswith("RUNTIME_ROOT="))
            runtime = Path(runtime_line.split("=", 1)[1])
            intake = {
                "software_full_name": "文本摘要命令行工具", "software_short_name": "摘要工具", "version": "V1.0",
                "rightsholder": {"type": "legal_person", "name": "开源测试组织", "id_type": "测试标识", "id_number": "TEST-E2E-ONLY"},
                "completion_date": "2026-08-11", "development_mode": "independent", "software_nature": "original",
                "publication": {"status": "unpublished"}, "rights_acquisition": "original",
                "rights_scope": {"type": "all", "detail": ""}, "ownership_notes": "合成测试数据。",
                "screenshot_mode": "user_supplied", "confirmed_by": "automated-test", "confirmed_at": "2026-08-11T00:00:00+08:00",
            }
            business = {
                "software_classification": "命令行工具", "software_purpose": "读取文本并生成规范化摘要",
                "target_users": "内部工作人员", "industry_domain": "通用软件工具",
                "development_purpose": "提高文本摘要处理效率", "development_environment": "跨平台 Python 环境",
                "development_tools": "Python 3.10+", "runtime_platform": "Windows、macOS、Linux",
                "runtime_support": "具备文件读写权限的计算机", "main_functions": (
                    "软件读取用户指定的文本文件，先检查路径、编码和内容是否有效，再按照确定性规则完成空白清理、长度统计、摘要生成和结果输出。"
                    "工作人员可从命令行输入文件路径与输出位置，查看处理进度、摘要正文、字符数量和保存结果；输入缺失、文件不可读或内容为空时，程序显示明确原因并保留重新执行入口。"
                    "处理过程中不修改原始文件，生成结果写入独立输出位置，并为每次任务保留输入名称、执行状态和完成提示。工作人员可以依据输出内容核对摘要是否对应原文，必要时修正输入后重新运行。"
                    "系统对路径格式、文件存在性、文本编码和输出权限分别校验，成功时显示摘要与保存位置，失败时区分输入错误、读取错误和写入错误。核心处理由源码中的读取、规范化、统计和输出函数完成，操作步骤、可见反馈与代码材料保持一致。"
                    "该工具面向需要批量整理文本的内部工作人员，用于减少重复复制和手工统计。结果仅来自实际输入内容，不自动补写不存在的信息；输出文件可继续用于归档、复核或后续分析。"
                ),
                "technical_features": "确定性处理、输入校验、明确错误状态", "startup": "运行命令行入口",
                "interface_structure": "命令参数区和结果输出区", "workflow_summary": "输入、处理、查看结果",
                "capabilities": [{"id": "CAP-core", "name": "摘要处理", "purpose": "生成文本摘要", "actor": "内部工作人员",
                    "entry": "命令行入口", "visible_elements": "输入参数和结果输出", "steps": ["输入文件路径", "执行摘要命令", "查看输出结果"],
                    "operation_type": "file_processing", "prerequisites": ["确认输入文件可读", "确认输出目录可写"],
                    "inputs": ["输入文件路径", "输出文件路径"], "outputs": ["摘要正文", "字符数量", "保存位置"],
                    "business_rules": ["不修改原始文件", "结果只来自当前输入内容"],
                    "error_cases": [{"condition": "路径不存在", "resolution": "核对路径后重新执行"},
                                    {"condition": "输出目录不可写", "resolution": "选择有写入权限的目录"}],
                    "data_scope": "仅处理当前命令指定的文本文件", "restrictions": ["输入文件必须存在"],
                    "success_feedback": "输出摘要、字符统计与保存位置", "error_feedback": "显示路径、读取或写入阶段的错误原因",
                    "evidence_ids": ["FILE-core", "RUNTIME-core"], "screenshot_ids": ["user-shot-001"]}],
                "faq": [{"question": "输入错误如何处理？", "answer": "按提示修改路径后重试。"}],
                "terms": [{"term": "摘要", "description": "规范化后的文本结果。"}],
                "confirmed_against_runtime": True, "generated_from_evidence": True,
            }
            (runtime / "work" / "一次性基础信息表.json").write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")
            (runtime / "work" / "business-understanding.json").write_text(json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8")

            screenshot_dir = runtime / "user-screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (1440, 900), "#f0f0f0")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 1439, 72), fill="#222222")
            for row in range(8):
                for col in range(10):
                    shade = 70 + (row * 17 + col * 11) % 150
                    draw.rectangle((40 + col * 135, 110 + row * 90, 150 + col * 135, 175 + row * 90), fill=(shade, shade, shade))
            draw.text((48, 24), "Summary CLI", fill="white")
            image.save(screenshot_dir / "01-core.png")

            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/product_workflow.py"), "generate", "--project", str(project)],
                text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=900,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            formal = output / "正式资料"
            expected = {
                "申请表信息.txt",
                "文本摘要命令行工具_操作手册.docx",
                "文本摘要命令行工具_操作手册.pdf",
                "文本摘要命令行工具-代码(全部).docx",
                "文本摘要命令行工具-代码(全部).pdf",
            }
            self.assertEqual({path.name for path in formal.iterdir() if path.is_file()}, expected)
            self.assertEqual({path.name for path in output.iterdir()}, {"正式资料"})


if __name__ == "__main__":
    unittest.main()
