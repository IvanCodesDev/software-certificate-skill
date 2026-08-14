from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import auto_select_source  # noqa: E402
import product_verify  # noqa: E402


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / name), *arguments],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def write_lines(path: Path, template: str, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(template.format(i=i) for i in range(count)), encoding="utf-8")


def analyze(root: Path, project: Path) -> dict:
    evidence, analysis = root / "evidence.json", root / "analysis.json"
    scan = run_script("scan_project.py", "--project", str(project), "--output", str(evidence))
    assert scan.returncode == 0, scan.stdout + scan.stderr
    analyzed = run_script("analyze_project.py", "--project", str(project),
                          "--evidence", str(evidence), "--output", str(analysis))
    assert analyzed.returncode == 0, analyzed.stdout + analyzed.stderr
    return json.loads(analysis.read_text(encoding="utf-8"))


def select(root: Path, project: Path, with_analysis: bool = True) -> tuple[dict, dict, subprocess.CompletedProcess]:
    manifest, report = root / "manifest.json", root / "report.json"
    arguments = ["--project", str(project), "--manifest", str(manifest), "--report", str(report)]
    if with_analysis:
        arguments += ["--analysis", str(root / "analysis.json")]
    selected = run_script("auto_select_source.py", *arguments)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    return (json.loads(manifest.read_text(encoding="utf-8")),
            json.loads(report.read_text(encoding="utf-8")), selected)


class ArchitectureScopeTests(unittest.TestCase):
    def test_frontend_repo_excludes_vendored_backend_for_ownership_review(self):
        with tempfile.TemporaryDirectory(prefix="softcert-scope-") as temp:
            root = Path(temp)
            project = root / "project"
            (project / "src" / "view").mkdir(parents=True)
            (project / "server").mkdir()
            (project / "package.json").write_text(json.dumps({
                "name": "demo-web", "version": "1.0.0",
                "dependencies": {"vue": "^3.5.0", "element-plus": "^2.12.0"},
                "devDependencies": {"vite": "^7.0.0"},
            }), encoding="utf-8")
            (project / "index.html").write_text("<!doctype html><div id='app'></div>", encoding="utf-8")
            (project / "src" / "App.vue").write_text(
                "<template><router-view /></template>\n" * 3, encoding="utf-8")
            (project / "src" / "view" / "list.vue").write_text(
                "\n".join(f"<div>row {i}</div>" for i in range(40)), encoding="utf-8")
            (project / "src" / "main.ts").write_text(
                "\n".join(f"console.log({i})" for i in range(30)), encoding="utf-8")
            # Vendored backend implementations that belong to another codebase.
            (project / "server" / "app.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n"
                + "\n".join(f"def api_{i}(): return {i}" for i in range(20)), encoding="utf-8")
            (project / "legacy_util.java").write_text(
                "public class LegacyUtil {\n" + "\n".join(f"  int f{i}() {{ return {i}; }}" for i in range(10)) + "\n}",
                encoding="utf-8")

            evidence = root / "evidence.json"
            analysis = root / "analysis.json"
            scan = run_script("scan_project.py", "--project", str(project), "--output", str(evidence))
            self.assertEqual(scan.returncode, 0, scan.stdout + scan.stderr)
            analyzed = run_script("analyze_project.py", "--project", str(project),
                                  "--evidence", str(evidence), "--output", str(analysis))
            self.assertEqual(analyzed.returncode, 0, analyzed.stdout + analyzed.stderr)
            scope = json.loads(analysis.read_text(encoding="utf-8"))["technology"]["architecture_scope"]
            self.assertEqual(scope["scope"], "frontend_only", scope)

            manifest = root / "manifest.json"
            report = root / "report.json"
            selected = run_script("auto_select_source.py", "--project", str(project),
                                  "--manifest", str(manifest), "--report", str(report),
                                  "--analysis", str(analysis))
            self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            report_data = json.loads(report.read_text(encoding="utf-8"))

            flagged = {item["path"] for item in report_data["ownership_review"]}
            self.assertIn("server/app.py", flagged)
            self.assertIn("legacy_util.java", flagged)
            self.assertEqual(manifest_data["selection_policy"]["architecture_scope"], "frontend_only")
            for path in flagged:
                self.assertNotIn(path, manifest_data["ordered_files"],
                                 "ownership-review files must stay out of the filing")
            decisions = {item["path"]: item for item in manifest_data["file_decisions"]}
            self.assertEqual(decisions["server/app.py"].get("reason"), "backend_ownership_review")

    def test_fullstack_python_backend_without_declared_dependencies(self):
        """A big frontend must not dilute an in-repo backend out of the filing.

        The backend here declares no requirements.txt and only one file
        imports Flask, so neither declared dependencies nor per-file import
        volume reach the old thresholds — only server-directory attribution
        keeps it in scope.
        """
        with tempfile.TemporaryDirectory(prefix="softcert-scope-fs-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({
                "name": "order-web", "version": "2.1.0",
                "dependencies": {"vue": "^3.5.0", "element-plus": "^2.12.0"},
                "devDependencies": {"vite": "^7.0.0"},
            }), encoding="utf-8")
            (project / "index.html").write_text("<!doctype html><div id='app'></div>", encoding="utf-8")
            write_lines(project / "src" / "App.vue", "<template>slot {i}</template>", 40)
            write_lines(project / "src" / "pages" / "order_page.vue", "<div>order row {i}</div>", 1200)
            write_lines(project / "src" / "pages" / "report_page.vue", "<div>report row {i}</div>", 1200)
            write_lines(project / "src" / "main.ts", "console.log({i})", 60)
            (project / "backend").mkdir()
            (project / "backend" / "app.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n"
                + "\n".join(f"def api_{i}(): return {i}" for i in range(58)), encoding="utf-8")
            write_lines(project / "backend" / "services" / "order_service.py", "def handle_{i}(): return {i}", 220)
            write_lines(project / "backend" / "models" / "order.py", "FIELD_{i} = {i}", 140)

            analysis = analyze(root, project)
            scope = analysis["technology"]["architecture_scope"]
            self.assertEqual(scope["scope"], "fullstack", scope)

            manifest_data, report_data, _ = select(root, project)
            self.assertEqual(report_data["ownership_review"], [])
            balance = manifest_data["selection_policy"]["side_balance"]
            self.assertIsNotNone(balance, manifest_data["selection_policy"])
            self.assertGreaterEqual(balance["backend_lines"], 300, balance)
            ordered = manifest_data["ordered_files"]
            self.assertTrue(any(path.startswith("backend/") for path in ordered), ordered)
            self.assertTrue(any(path.startswith("backend/") for path in ordered[:3]),
                            "interleaving must surface backend files early: " + repr(ordered[:3]))

    def test_fullstack_node_backend_in_server_dir(self):
        """A plain Node backend without its own package.json still counts."""
        with tempfile.TemporaryDirectory(prefix="softcert-scope-node-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({
                "name": "report-web", "version": "1.4.0",
                "dependencies": {"react": "^19.0.0"},
            }), encoding="utf-8")
            write_lines(project / "src" / "pages" / "home_page.jsx", "export const Row{i} = () => <p>{i}</p>;", 500)
            write_lines(project / "src" / "app.jsx", "export const App{i} = {i};", 100)
            (project / "server").mkdir()
            (project / "server" / "index.js").write_text(
                "const express = require('express');\nconst app = express();\n"
                + "\n".join(f"app.get('/api/{i}', (req, res) => res.json({i}));" for i in range(78)),
                encoding="utf-8")
            write_lines(project / "server" / "routes" / "order_route.js", "exports.handler{i} = () => {i};", 180)
            (project / "server" / "db" / "query.js").parent.mkdir(parents=True, exist_ok=True)
            (project / "server" / "db" / "query.js").write_text(
                "const mysql = require('mysql2');\n"
                + "\n".join(f"exports.query{i} = () => {i};" for i in range(89)), encoding="utf-8")

            analysis = analyze(root, project)
            scope = analysis["technology"]["architecture_scope"]
            self.assertEqual(scope["scope"], "fullstack", scope)

            manifest_data, _, _ = select(root, project)
            balance = manifest_data["selection_policy"]["side_balance"]
            self.assertIsNotNone(balance)
            self.assertGreaterEqual(balance["backend_lines"], 300, balance)
            self.assertTrue(any(path.startswith("server/") for path in manifest_data["ordered_files"]))

    def test_java_backend_detected_by_imports_not_dependencies(self):
        """Spring/Dubbo services outside a `server/` dir must still be backend.

        Mirrors a Maven microservice repo whose Java lives in
        `<module>/src/main/java` and whose frontend ships vendored JS bundles
        big enough to push the backend below any line-ratio threshold.
        """
        with tempfile.TemporaryDirectory(prefix="softcert-scope-java-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir(parents=True)
            (project / "pom.xml").write_text(
                "<project><artifactId>mall-parent</artifactId><version>1.0</version></project>",
                encoding="utf-8")
            (project / "index.html").write_text("<!doctype html><div id='app'></div>", encoding="utf-8")
            (project / "package.json").write_text(json.dumps({
                "name": "mall-web", "version": "1.0.0", "dependencies": {"vue": "^2.6.0"},
            }), encoding="utf-8")
            write_lines(project / "web" / "src" / "App.vue", "<template>row {i}</template>", 300)
            # Vendored frontend bundles dominate the line count.
            write_lines(project / "web" / "static" / "js" / "vendor.js", "var chunk{i} = {i};", 6000)
            for module in ("order", "item", "cart"):
                controller = (project / f"mall-{module}-service" / "src" / "main" / "java"
                              / f"{module.title()}Controller.java")
                controller.parent.mkdir(parents=True, exist_ok=True)
                body = "\n".join(f"    public Object query{i}() {{ return null; }}" for i in range(160))
                controller.write_text(
                    "import org.springframework.web.bind.annotation.RestController;\n"
                    "import org.apache.dubbo.config.annotation.Reference;\n"
                    f"public class {module.title()}Controller {{\n{body}\n}}\n", encoding="utf-8")

            analysis = analyze(root, project)
            scope = analysis["technology"]["architecture_scope"]
            self.assertEqual(scope["scope"], "fullstack", scope)
            self.assertGreaterEqual(scope["backend_implementation"]["attributed_lines"], 300, scope)
            # The ratio alone must not be what carries the decision.
            self.assertLess(scope["backend_language_ratio"], 0.15, scope)

            manifest_data, _, _ = select(root, project)
            balance = manifest_data["selection_policy"]["side_balance"]
            self.assertIsNotNone(balance)
            self.assertGreater(balance["backend_files"], 0, balance)

    def test_backend_quota_applies_without_analysis(self):
        """Selector fail-safe: both sides present => balanced, even with no scope."""
        with tempfile.TemporaryDirectory(prefix="softcert-scope-nofile-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir(parents=True)
            write_lines(project / "src" / "pages" / "alpha_page.tsx", "export const A{i} = () => <i>{i}</i>;", 400)
            write_lines(project / "backend" / "report_service.py", "def report_{i}(): return {i}", 350)

            manifest_data, _, _ = select(root, project, with_analysis=False)
            balance = manifest_data["selection_policy"]["side_balance"]
            self.assertIsNotNone(balance, manifest_data["selection_policy"])
            self.assertEqual(balance["trigger"], "both_sides_detected")
            self.assertGreaterEqual(balance["backend_lines"], 300, balance)
            ordered = manifest_data["ordered_files"]
            self.assertIn("backend/report_service.py", ordered)
            self.assertIn("src/pages/alpha_page.tsx", ordered)

    def test_plain_python_tool_stays_unclassified_and_unaffected(self):
        with tempfile.TemporaryDirectory(prefix="softcert-scope-cli-") as temp:
            root = Path(temp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "tool.py").write_text(
                "\n".join(f"def step_{i}(): return {i}" for i in range(60)), encoding="utf-8")
            evidence, analysis = root / "evidence.json", root / "analysis.json"
            run_script("scan_project.py", "--project", str(project), "--output", str(evidence))
            run_script("analyze_project.py", "--project", str(project),
                       "--evidence", str(evidence), "--output", str(analysis))
            scope = json.loads(analysis.read_text(encoding="utf-8"))["technology"]["architecture_scope"]
            self.assertEqual(scope["scope"], "unclassified", scope)

            manifest, report = root / "manifest.json", root / "report.json"
            selected = run_script("auto_select_source.py", "--project", str(project),
                                  "--manifest", str(manifest), "--report", str(report),
                                  "--analysis", str(analysis))
            self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_data["ownership_review"], [])
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("src/tool.py", manifest_data["ordered_files"])


class ApplicationScopeTests(unittest.TestCase):
    @staticmethod
    def module_pom(path: Path, artifact: str, parent: str | None = None,
                   packaging: str = "jar", group: str = "com.demo.app") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        parent_block = (f"<parent><groupId>{group}</groupId><artifactId>{parent}</artifactId>"
                        f"<version>1.0</version></parent>") if parent else ""
        path.write_text(
            f"<project>{parent_block}<groupId>{group}</groupId>"
            f"<artifactId>{artifact}</artifactId><packaging>{packaging}</packaging></project>",
            encoding="utf-8")

    def test_maven_reactor_counts_as_one_application(self):
        """A microservice monorepo must not ask which of N modules to file."""
        with tempfile.TemporaryDirectory(prefix="softcert-reactor-") as temp:
            project = Path(temp) / "project"
            project.mkdir(parents=True)
            self.module_pom(project / "app-parent" / "pom.xml", "app-parent",
                            parent="spring-boot-starter-parent", packaging="pom")
            for module in ("app-order", "app-user", "app-search", "app-web"):
                self.module_pom(project / module / "pom.xml", module, parent="app-parent")
                write_lines(project / module / "src" / "main" / "java" / "Svc.java",
                            "class C{i} {{ }}", 40)
            self.assertEqual(len(auto_select_source.independent_scopes(project)), 1,
                             auto_select_source.independent_scopes(project))

    def test_two_unrelated_applications_still_require_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="softcert-multiapp-") as temp:
            project = Path(temp) / "project"
            project.mkdir(parents=True)
            self.module_pom(project / "billing" / "pom.xml", "billing",
                            parent="spring-boot-starter-parent", group="com.one.billing")
            self.module_pom(project / "crm" / "pom.xml", "crm",
                            parent="spring-boot-starter-parent", group="com.two.crm")
            self.assertEqual(len(auto_select_source.independent_scopes(project)), 2)

    def test_declared_workspace_packages_collapse_to_one_scope(self):
        with tempfile.TemporaryDirectory(prefix="softcert-workspace-") as temp:
            project = Path(temp) / "project"
            project.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({
                "name": "suite", "version": "1.0.0", "workspaces": ["web", "api"],
            }), encoding="utf-8")
            for name in ("web", "api"):
                (project / name).mkdir()
                (project / name / "package.json").write_text(
                    json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
            scopes = auto_select_source.independent_scopes(project)
            self.assertEqual(len(scopes), 1, scopes)

    def test_two_unrelated_node_packages_still_require_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="softcert-twonode-") as temp:
            project = Path(temp) / "project"
            project.mkdir(parents=True)
            for name in ("storefront", "intranet"):
                (project / name).mkdir()
                (project / name / "package.json").write_text(
                    json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
            self.assertEqual(len(auto_select_source.independent_scopes(project)), 2)


class BackendCoverageGateTests(unittest.TestCase):
    @staticmethod
    def provenance(front_last: int = 150) -> dict:
        return {
            "selection_policy": {"architecture_scope": "fullstack"},
            "file_decisions": [
                {"path": "src/a.vue", "side": "frontend"},
                {"path": "backend/b.py", "side": "backend"},
            ],
            "files": [
                {"path": "src/a.vue", "output_start_line": 1, "output_end_line": 100},
                {"path": "backend/b.py", "output_start_line": 101, "output_end_line": 200},
            ],
            "filing_groups": {
                "front_30": {"first_output_line": 1, "last_output_line": front_last},
                "back_30": {"first_output_line": 51, "last_output_line": 200},
            },
        }

    def test_passes_when_every_volume_overlaps_backend(self):
        covered, detail = product_verify.backend_side_coverage(self.provenance())
        self.assertTrue(covered, detail)

    def test_fails_when_one_volume_misses_backend(self):
        covered, detail = product_verify.backend_side_coverage(self.provenance(front_last=90))
        self.assertFalse(covered)
        self.assertIn("front_30", detail)

    def test_fails_when_no_backend_file_composed(self):
        data = self.provenance()
        data["files"] = [item for item in data["files"] if item["path"] != "backend/b.py"]
        covered, detail = product_verify.backend_side_coverage(data)
        self.assertFalse(covered)
        self.assertIn("未纳入任何后端源文件", detail)


if __name__ == "__main__":
    unittest.main()
