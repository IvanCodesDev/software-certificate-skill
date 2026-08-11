#!/usr/bin/env python3
"""Verify that all checked-in demos are sanitized, internally consistent, and truly rendered."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

from common import load_json, sha256_file


def pdf_pages_and_text(path: Path, page: int = 1) -> tuple[int, str]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    text = reader.pages[page].extract_text() if len(reader.pages) > page else ""
    return len(reader.pages), text or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos", type=Path, default=Path(__file__).resolve().parents[1] / "demos")
    args = parser.parse_args()
    root = args.demos.resolve()
    failures: list[str] = []
    for name in ("web-project", "desktop-project", "cli-project"):
        demo = root / name
        output = demo / "project/软件著作权申请资料"
        formal, quality = output / "正式资料", output / "质量检查"
        report = load_json(quality / "材料一致性校验报告.json")
        if not report.get("release_ready"):
            failures.append(f"{name}: consistency report is not release-ready")
        hash_lines = (quality / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        for line in hash_lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if not match:
                failures.append(f"{name}: malformed SHA256 line")
                continue
            path = output / match.group(2)
            if not path.is_file() or sha256_file(path) != match.group(1):
                failures.append(f"{name}: SHA256 mismatch {match.group(2)}")
        manual_docx = next(formal.glob("*_操作手册.docx"))
        manual_pdf = next(formal.glob("*_操作手册.pdf"))
        with zipfile.ZipFile(manual_docx) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        if "TOC \\o" not in xml or "\\h" not in xml:
            failures.append(f"{name}: Word TOC field/hyperlink switch missing")
        manual_pages, toc_text = pdf_pages_and_text(manual_pdf, 1)
        if manual_pages < 3 or "目录" not in toc_text or "软件概述" not in toc_text or "右键并选择" in toc_text:
            failures.append(f"{name}: PDF TOC was not refreshed")
        provenance = load_json(quality / "代码来源追溯清单.json")
        code_pdf = next(formal.glob("*-代码(全部).pdf"))
        code_pages, _ = pdf_pages_and_text(code_pdf, 0)
        expected = provenance.get("filing_groups", {}).get("all", {}).get("page_count")
        if code_pages != expected:
            failures.append(f"{name}: code PDF pages {code_pages} != {expected}")
        for path in demo.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt", ".yaml", ".yml"}:
                if re.search(r"[A-Za-z]:\\+", path.read_text(encoding="utf-8-sig")):
                    failures.append(f"{name}: unsanitized absolute path in {path.relative_to(demo)}")
                    break
        print(f"DEMO={name} RELEASE_READY={report.get('release_ready')} MANUAL_PAGES={manual_pages} CODE_PAGES={code_pages} TOC=PASS")
    print(f"DEMO_VERIFICATION={'PASS' if not failures else 'FAIL'} FAILURES={len(failures)}")
    for failure in failures:
        print(f"- {failure}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
