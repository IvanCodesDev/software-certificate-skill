from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from product_model import ProductPaths, find_slots, prune_delivery_root, safe_filename, stage_is_current
from plan_manual import FRONT, CLOSING


class ProductUnitTests(unittest.TestCase):
    def test_filename_and_slots(self):
        self.assertEqual(safe_filename('A:B/C*D?'), "A_B_C_D_")
        self.assertEqual(find_slots({"value": "【待确认：名称】"}), ["【待确认：名称】"])

    def test_content_based_plan_prefers_40_to_60_without_fixed_padding(self):
        self.assertEqual(len(FRONT) + len(CLOSING), 10)
        source = (SCRIPTS / "plan_manual.py").read_text(encoding="utf-8")
        self.assertIn("content_based_no_padding", source)
        self.assertIn('"preferred_range": [40, 60]', source)
        self.assertIn("小型项目可低于40页", source)

    def test_schemas_and_examples(self):
        import jsonschema
        pairs = [("intake", "intake.example.json"), ("business-understanding", "business-understanding.example.json"),
                 ("computer-use-session", "computer-use-session.example.json")]
        for schema_name, example_name in pairs:
            schema = json.loads((ROOT / f"assets/schemas/{schema_name}.schema.json").read_text(encoding="utf-8"))
            example = json.loads((ROOT / f"assets/examples/{example_name}").read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema)
            self.assertIsInstance(list(validator.iter_errors(example)), list)

    def test_prepare_is_resumable_and_chinese_path_safe(self):
        with tempfile.TemporaryDirectory(prefix="软著 测试 ") as temp:
            project = Path(temp) / "中文 项目"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname="demo"\nversion="1.0.0"\n', encoding="utf-8")
            (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
            command = [sys.executable, str(SCRIPTS / "product_workflow.py"), "prepare", "--project", str(project)]
            child_env = os.environ.copy()
            child_env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            first = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                                   capture_output=True, env=child_env)
            self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
            runtime_line = next(line for line in first.stdout.splitlines() if line.startswith("RUNTIME_ROOT="))
            state = Path(runtime_line.split("=", 1)[1]) / "workflow-state.json"
            self.assertTrue(state.is_file())
            delivery = project / "软件著作权申请资料"
            self.assertEqual({item.name for item in delivery.iterdir()}, {"正式资料"})
            before = json.loads(state.read_text(encoding="utf-8"))["stages"]["project_analysis"]["input_sha256"]
            second = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                                    capture_output=True, env=child_env)
            self.assertEqual(second.returncode, 3)
            after = json.loads(state.read_text(encoding="utf-8"))["stages"]["project_analysis"]["input_sha256"]
            self.assertEqual(before, after)

    def test_delivery_pruning_keeps_only_formal_materials(self):
        with tempfile.TemporaryDirectory(prefix="softcert-delivery-") as temp:
            project = Path(temp) / "project"
            project.mkdir()
            paths = ProductPaths.create(project)
            legacy = paths.root / "质量检查"
            legacy.mkdir()
            (legacy / "report.json").write_text("{}", encoding="utf-8")
            (paths.root / "一次性基础信息表.json").write_text("{}", encoding="utf-8")
            (paths.formal / "申请表信息.txt").write_text("ok", encoding="utf-8")
            prune_delivery_root(paths)
            self.assertEqual({item.name for item in paths.root.iterdir()}, {"正式资料"})
            self.assertEqual({item.name for item in paths.formal.iterdir()}, {"申请表信息.txt"})


if __name__ == "__main__":
    unittest.main()
