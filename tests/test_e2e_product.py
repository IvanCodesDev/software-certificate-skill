from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RUN_DOCUMENT_E2E") == "1", "set RUN_DOCUMENT_E2E=1 with Word/LibreOffice available")
class ProductEndToEndTests(unittest.TestCase):
    def test_cli_demo_release(self):
        with tempfile.TemporaryDirectory(prefix="software-certificate-e2e-") as temp:
            command = [sys.executable, str(ROOT / "scripts/build_demos.py"), "--output", temp, "--only", "cli-project"]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=900)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            formal = Path(temp) / "cli-project/project/软件著作权申请资料/正式资料"
            self.assertTrue((formal / "申请表信息.txt").is_file())
            self.assertTrue(any(formal.glob("*_操作手册.docx")))
            self.assertTrue(any(formal.glob("*_操作手册.pdf")))
            self.assertTrue(any(formal.glob("*-代码(全部).pdf")))


if __name__ == "__main__":
    unittest.main()
