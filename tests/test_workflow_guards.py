from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from product_model import ProductPaths
from product_workflow import provenance_density, screenshot_index, screenshot_policy
from auto_select_source import scan_forbidden_markers


class WorkflowGuardTests(unittest.TestCase):
    def test_formal_capture_is_required_by_default(self):
        self.assertEqual(screenshot_policy({}), "required")
        self.assertEqual(screenshot_policy({"screenshot_policy": "draft_allowed"}), "draft_allowed")

    def test_configured_source_marker_is_detected_without_rewriting_source(self):
        findings = scan_forbidden_markers("package demo\n// upstream: stylefeng\n", ["stylefeng"])
        self.assertEqual(findings[0]["marker"], "stylefeng")
        self.assertEqual(findings[0]["line"], 2)

    def test_nonfinal_effective_density_is_hard_gate(self):
        self.assertEqual(provenance_density({"lines_per_page": 50,
                                             "pages": [{"effective_lines": 50}, {"effective_lines": 50},
                                                       {"effective_lines": 12}]}),
                         (True, "非末页均达到50行；末页12行（末页例外）"))
        ok, detail = provenance_density({"lines_per_page": 50,
                                         "pages": [{"effective_lines": 50}, {"effective_lines": 49},
                                                   {"effective_lines": 12}]})
        self.assertFalse(ok)
        self.assertIn("[2]", detail)

    def test_required_skip_stops_before_placeholder_generation(self):
        with tempfile.TemporaryDirectory(prefix="softcert-workflow-guards-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
            paths = ProductPaths.create(project, root / "delivery")
            business_path = paths.work / "business-understanding.json"
            business_path.write_text(json.dumps({
                "target_users": "测试员",
                "capabilities": [{"name": "首页", "actor": "测试员", "route": "/", "evidence_ids": ["CAP-home"]}],
            }, ensure_ascii=False), encoding="utf-8")
            facts = {
                "screenshot_mode": "skip", "screenshot_policy": "required",
                "screenshot_base_url": "http://127.0.0.1:8080",
            }
            index, state = screenshot_index(paths, facts, business_path)
            self.assertEqual(state, "awaiting_capture")
            data = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(data["state"], "awaiting_capture")
            self.assertIn("禁止跳过截图", data["blocking_reason"])

    def test_plan_refresh_archives_stale_capture_state(self):
        with tempfile.TemporaryDirectory(prefix="softcert-plan-refresh-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
            paths = ProductPaths.create(project, root / "delivery")
            business_path = paths.work / "business-understanding.json"
            business_path.write_text(json.dumps({"target_users": "测试员", "capabilities": [
                {"name": "首页", "actor": "测试员", "route": "/", "evidence_ids": ["CAP-home"]}
            ]}, ensure_ascii=False), encoding="utf-8")
            facts = {"screenshot_mode": "skip", "screenshot_policy": "draft_allowed",
                     "screenshot_base_url": "http://127.0.0.1:8080"}
            first, _ = screenshot_index(paths, facts, business_path)
            first_plan = json.loads((paths.work / "screenshot-plan.json").read_text(encoding="utf-8"))
            business_path.write_text(json.dumps({"target_users": "测试员", "capabilities": [
                {"name": "详情", "actor": "测试员", "route": "/detail", "evidence_ids": ["CAP-detail"]}
            ]}, ensure_ascii=False), encoding="utf-8")
            second, state = screenshot_index(paths, facts, business_path)
            second_plan = json.loads((paths.work / "screenshot-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(state, "skipped_by_user")
            self.assertNotEqual(first_plan["input_sha256"], second_plan["input_sha256"])
            self.assertEqual(second_plan["captures"][0]["route"], "/detail")
            self.assertTrue(list((paths.work / "stale-screenshot-runs").iterdir()))

    def test_server_health_url_populates_capture_base_url(self):
        with tempfile.TemporaryDirectory(prefix="softcert-server-plan-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
            paths = ProductPaths.create(project, root / "delivery")
            business_path = paths.work / "business-understanding.json"
            business_path.write_text(json.dumps({"target_users": "测试员", "capabilities": [
                {"name": "首页", "actor": "测试员", "route": "/", "evidence_ids": ["CAP-home"]}
            ]}, ensure_ascii=False), encoding="utf-8")
            facts = {"screenshot_mode": "skip", "screenshot_policy": "draft_allowed",
                     "screenshot_server": {"command": "java -jar app.jar",
                                           "cwd": ".", "health_url": "http://127.0.0.1:8080/login",
                                           "startup_timeout_seconds": 90}}
            screenshot_index(paths, facts, business_path)
            plan = json.loads((paths.work / "screenshot-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["base_url"], "http://127.0.0.1:8080")
            self.assertEqual(plan["server"]["command"], "java -jar app.jar")


if __name__ == "__main__":
    unittest.main()
