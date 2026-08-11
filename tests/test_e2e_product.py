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
                text=True, capture_output=True, timeout=120,
            )
            self.assertIn(prepare.returncode, {0, 3}, prepare.stdout + prepare.stderr)

            output = project / "软件著作权申请资料"
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
                "runtime_support": "具备文件读写权限的计算机", "main_functions": "读取用户指定文本，校验输入内容，执行规范化处理并输出摘要结果和异常提示。",
                "technical_features": "确定性处理、输入校验、明确错误状态", "startup": "运行命令行入口",
                "interface_structure": "命令参数区和结果输出区", "workflow_summary": "输入、处理、查看结果",
                "capabilities": [{"id": "CAP-core", "name": "摘要处理", "purpose": "生成文本摘要", "actor": "内部工作人员",
                    "entry": "命令行入口", "visible_elements": "输入参数和结果输出", "steps": ["输入文件路径", "执行摘要命令", "查看输出结果"],
                    "restrictions": ["输入文件必须存在"], "success_feedback": "输出摘要", "error_feedback": "显示错误原因",
                    "evidence_ids": ["FILE-core", "RUNTIME-core"], "screenshot_ids": ["user-shot-001"]}],
                "faq": [{"question": "输入错误如何处理？", "answer": "按提示修改路径后重试。"}],
                "terms": [{"term": "摘要", "description": "规范化后的文本结果。"}],
                "confirmed_against_runtime": True, "generated_from_evidence": True,
            }
            (output / "一次性基础信息表.json").write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")
            (output / ".工作区" / "business-understanding.json").write_text(json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8")

            screenshot_dir = output / "用户截图"
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
                text=True, capture_output=True, timeout=900,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            formal = output / "正式资料"
            self.assertTrue((formal / "申请表信息.txt").is_file())
            self.assertTrue(any(formal.glob("*_操作手册.docx")))
            self.assertTrue(any(formal.glob("*_操作手册.pdf")))
            self.assertTrue(any(formal.glob("*-代码(全部).pdf")))


if __name__ == "__main__":
    unittest.main()
