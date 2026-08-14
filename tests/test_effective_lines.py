from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class EffectiveLineTests(unittest.TestCase):
    def test_blank_lines_never_enter_the_filing_corpus(self):
        with tempfile.TemporaryDirectory(prefix="softcert-effective-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            # 3 blank lines for every 2 code lines: a page built naively from
            # this file would carry ~40% blanks and fail the reviewer count.
            blocks = []
            for index in range(1, 131):
                blocks.append(f"def handler_{index}(payload):")
                blocks.append(f"    return normalize(payload, key={index})")
                blocks.append("")
                blocks.append("   ")
                blocks.append("")
            (project / "engine.py").write_text("\n".join(blocks) + "\n", encoding="utf-8")
            manifest = project / "manifest.json"
            manifest.write_text(json.dumps({
                "include": ["**/*.py"], "exclude": [], "ordered_files": ["engine.py"],
                "review": {"confirmed_by": "unit", "confirmed_at": "2026-08-12T00:00:00+08:00",
                           "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                           "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
            compose = subprocess.run([
                sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
                "--manifest", str(manifest), "--output-dir", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)

            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["blank_lines_excluded"], 390)
            self.assertEqual(provenance["full_line_count"], 260)
            self.assertEqual(provenance["original_line_count"], 650)

            # Pages are separated by "\n\f\n"; split on that explicitly since
            # str.splitlines() also treats the form feed as a line boundary.
            raw = (output / "source-full.txt").read_text(encoding="utf-8").rstrip("\n")
            lines = [line for chunk in raw.split("\n\f\n") for line in chunk.split("\n")]
            self.assertEqual(len(lines), 260)
            self.assertTrue(all(line.strip() for line in lines), "corpus must contain no blank lines")

            pages = provenance["pages"]
            self.assertTrue(all(page["effective_lines"] == 50 for page in pages[:-1]),
                            "every page except the last must carry the full quota")

            # Traceability survives the removal: material lines still map to
            # their original (non-consecutive) source line numbers.
            mapping = provenance["line_mapping"]
            self.assertEqual(mapping[0]["source_line"], 1)
            self.assertEqual(mapping[1]["source_line"], 2)
            self.assertEqual(mapping[2]["source_line"], 6)

    def test_wrapped_long_lines_still_count_as_one_effective_line(self):
        """A reviewer counts source lines, not rendered rows."""
        with tempfile.TemporaryDirectory(prefix="softcert-wrap-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            lines = []
            for index in range(1, 221):
                if index % 3 == 0:
                    payload = ", ".join(f"'field_{n}': value_{n}" for n in range(12))
                    lines.append(f"CONFIG_{index} = {{{payload}}}")
                else:
                    lines.append(f"result_{index} = compute({index})")
            (project / "engine.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
            manifest = project / "manifest.json"
            manifest.write_text(json.dumps({
                "include": ["**/*.py"], "exclude": [], "ordered_files": ["engine.py"],
                "review": {"confirmed_by": "unit", "confirmed_at": "2026-08-12T00:00:00+08:00",
                           "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                           "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
            compose = subprocess.run([
                sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
                "--manifest", str(manifest), "--output-dir", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            pages = provenance["pages"]

            wrapped_rows = sum(1 for item in provenance["line_mapping"] if item["segment"] > 1)
            self.assertGreater(wrapped_rows, 0, "fixture must actually produce wrapped rows")
            self.assertTrue(all(page["effective_lines"] == 50 for page in pages[:-1]),
                            "wrapped rows must not steal quota from effective lines")
            self.assertTrue(any(page["line_count"] > page["effective_lines"] for page in pages),
                            "at least one page should render more rows than source lines")
            self.assertGreaterEqual(provenance["rows_per_page"], 50)
            # A wrapped line must never straddle a page boundary.
            for page in pages:
                start = page["start_output_line"] - 1
                self.assertEqual(provenance["line_mapping"][start]["segment"], 1)


if __name__ == "__main__":
    unittest.main()
