from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def direct_renderer_ready() -> bool:
    """The renderer needs reportlab plus TrueType ASCII and CJK fonts."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s')\n"
         "import render_code_pdf as r\n"
         "ok = r.register_first('ProbeCJK', r.CJK_CANDIDATES, 'SOFTCERT_CODE_CJK_FONT') and "
         "r.register_first('ProbeMono', r.ASCII_CANDIDATES, 'SOFTCERT_CODE_ASCII_FONT')\n"
         "sys.exit(0 if ok else 4)" % SCRIPTS],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    return probe.returncode == 0


RENDERER_READY = direct_renderer_ready()


@unittest.skipUnless(RENDERER_READY, "reportlab or TrueType CJK/mono fonts unavailable")
class DirectCodePdfTests(unittest.TestCase):
    def test_grid_pdf_matches_composed_pages_and_is_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="softcert-direct-pdf-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            lines = []
            for index in range(1, 161):
                if index % 40 == 0:
                    lines.append(f"    # 校验第{index}条库存记录并同步审计日志，超限时触发预警。")
                else:
                    lines.append(f"value_{index} = process_item({index}, mode='standard')")
            (project / "engine.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
            manifest = project / "manifest.json"
            manifest.write_text(json.dumps({
                "include": ["**/*.py"], "exclude": [], "ordered_files": ["engine.py"],
                "review": {"confirmed_by": "unit", "confirmed_at": "2026-08-12T00:00:00+08:00",
                           "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                           "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
            facts = root / "facts.json"
            facts.write_text(json.dumps({"software_full_name": "直绘测试系统", "version": "V1.0"},
                                        ensure_ascii=False), encoding="utf-8")
            compose = subprocess.run([
                sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
                "--manifest", str(manifest), "--facts", str(facts), "--output-dir", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            expected_pages = provenance["filing_groups"]["all"]["page_count"]

            digests = []
            for round_no in (1, 2):
                pdf = output / f"direct-{round_no}.pdf"
                report_path = output / f"direct-{round_no}.json"
                render = subprocess.run([
                    sys.executable, str(SCRIPTS / "render_code_pdf.py"),
                    "--provenance", str(output / "source-provenance.json"), "--facts", str(facts),
                    "--volume", "all", "--pdf", str(pdf), "--report", str(report_path),
                    "--expected-pages", str(expected_pages),
                ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
                self.assertEqual(render.returncode, 0, render.stdout + render.stderr)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["pdf_pages"], expected_pages)
                reader = PdfReader(str(pdf))
                self.assertEqual(len(reader.pages), expected_pages)
                first_page_text = reader.pages[0].extract_text()
                self.assertIn("process_item", first_page_text)
                # pypdf splits extracted text at font-run boundaries.
                compact = first_page_text.replace(" ", "").replace("\n", "")
                self.assertIn("直绘测试系统V1.0", compact)
                self.assertIn("第1页共4页", compact)
                digests.append(__import__("hashlib").sha256(pdf.read_bytes()).hexdigest())
            self.assertEqual(digests[0], digests[1], "direct rendering must be byte-stable")


if __name__ == "__main__":
    unittest.main()
