from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def explicit_docx_pages(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
    return xml.count('w:type="page"') + 1


@unittest.skipUnless(os.environ.get("RUN_DOCUMENT_E2E") == "1", "set RUN_DOCUMENT_E2E=1 with LibreOffice available")
class SourceSplitEndToEndTests(unittest.TestCase):
    def test_front_back_30_are_real_and_continuous(self):
        with tempfile.TemporaryDirectory(prefix="softcert-split-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            lines = []
            for index in range(1, 3052):
                suffix = ("_LONG_SEGMENT" * 40) if index % 101 == 0 else ""
                lines.append(f"value_{index} = process_item({index}, '{suffix}')")
            source = project / "engine.py"
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            manifest = project / "manifest.json"
            manifest.write_text(json.dumps({
                "include": ["**/*.py"], "exclude": [], "ordered_files": ["engine.py"],
                "review": {"confirmed_by": "e2e", "confirmed_at": "2026-08-11T00:00:00+08:00",
                           "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                           "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
            compose = subprocess.run([
                sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
                "--manifest", str(manifest), "--output-dir", str(output), "--max-chars", "88",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(provenance["full_page_count"], 60)
            front = provenance["filing_groups"]["front_30"]["logical_source_pages"]
            back = provenance["filing_groups"]["back_30"]["logical_source_pages"]
            self.assertEqual(front, list(range(1, 31)))
            self.assertEqual(back, list(range(provenance["full_page_count"] - 29, provenance["full_page_count"] + 1)))
            self.assertEqual(back[-1], provenance["full_page_count"])
            self.assertEqual(provenance["filing_groups"]["back_30"]["last_output_line"],
                             provenance["full_line_count"])
            full_lines = [line for line in (output / "source-full.txt").read_text(encoding="utf-8").splitlines()
                          if line != "\f"]
            back_lines = [line for line in (output / "source-back-30.txt").read_text(encoding="utf-8").splitlines()
                          if line != "\f"]
            self.assertEqual(back_lines[-1], full_lines[-1])
            for name in ("source-front-30", "source-back-30"):
                docx, pdf = output / f"{name}.docx", output / f"{name}.pdf"
                report = output / f"{name}.render.json"
                rendered = output / f"{name}-pages"
                conversion = subprocess.run([
                    sys.executable, str(SCRIPTS / "convert_document.py"), "--input", str(docx),
                    "--pdf", str(pdf), "--report", str(report), "--render-dir", str(rendered),
                    "--expected-pages", "30", "--timeout-seconds", "180",
                ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                self.assertEqual(conversion.returncode, 0, conversion.stdout + conversion.stderr)
                self.assertEqual(explicit_docx_pages(docx), 30)
                self.assertEqual(len(PdfReader(str(pdf)).pages), 30)
                model = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(model["pdf_pages"], 30)
                self.assertEqual(model["rendered_pages"], 30)
                self.assertTrue(model["conversion"]["engine"].startswith("LibreOffice"))
                self.assertEqual(model["conversion_attempts"][0]["engine"], "LibreOffice")
                self.assertEqual(model["conversion_attempts"][0]["status"], "pass")
                self.assertTrue(Path(model["conversion_attempts"][0]["diagnostic"]).is_file())
            selected_text = (output / "source-full.txt").read_text(encoding="utf-8")
            self.assertLessEqual(max(len(line) for line in selected_text.splitlines()), 88)


if __name__ == "__main__":
    unittest.main()
