#!/usr/bin/env python3
"""Draw code filing volumes as PDF directly on a deterministic line grid.

Unlike the office-suite conversion path, pagination here is correct by
construction: every page draws exactly the lines composed by compose_code.py
at fixed positions, with fonts resolved from the local system and embedded as
subsets. No layout engine re-flows the text, so the page count can never
drift. Exit code 4 signals an unusable environment (missing reportlab or no
TrueType CJK font) so the workflow can fall back to office conversion.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from common import load_json, now_iso, save_json, sha256_file
from compose_code import (MARGIN_LEFT_MM, MARGIN_TOP_MM, MM_TO_PT, PAGE_HEIGHT_MM,
                          PAGE_WIDTH_MM, char_columns)

try:
    from reportlab.lib.colors import black
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - environment gate
    print("RENDER_UNAVAILABLE=reportlab is not installed", file=sys.stderr)
    raise SystemExit(4)


PAGE_WIDTH_PT = PAGE_WIDTH_MM * MM_TO_PT
PAGE_HEIGHT_PT = PAGE_HEIGHT_MM * MM_TO_PT
MARGIN_TOP_PT = MARGIN_TOP_MM * MM_TO_PT
MARGIN_LEFT_PT = MARGIN_LEFT_MM * MM_TO_PT
HEADER_BASELINE_FROM_TOP_PT = 15 * MM_TO_PT + 7.2
FOOTER_BASELINE_FROM_BOTTOM_PT = 17.5 * MM_TO_PT - 2.0
HEADER_FOOTER_SIZE = 9.0

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
# (font path, TTC subfont index). CFF-flavoured collections such as Ubuntu's
# NotoSansCJK cannot be parsed by reportlab's TTFont, so they are not listed.
CJK_CANDIDATES = [
    (WINDOWS_FONTS / "simsun.ttc", 0),
    (WINDOWS_FONTS / "simhei.ttf", None),
    (WINDOWS_FONTS / "simfang.ttf", None),
    (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
    (Path("/usr/share/fonts/truetype/arphic/uming.ttc"), 0),
    (Path("/System/Library/Fonts/STHeiti Light.ttc"), 0),
]
ASCII_CANDIDATES = [
    (WINDOWS_FONTS / "consola.ttf", None),
    (WINDOWS_FONTS / "CascadiaMono.ttf", None),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"), None),
    (Path("/System/Library/Fonts/Menlo.ttc"), 0),
]
LATIN_TEXT_CANDIDATES = [
    (WINDOWS_FONTS / "times.ttf", None),
    (Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"), None),
]


def register_first(logical_name: str, candidates: list[tuple[Path, int | None]],
                   override_env: str) -> str | None:
    override = os.environ.get(override_env)
    if override:
        candidates = [(Path(override), 0 if override.lower().endswith(".ttc") else None)] + candidates
    for path, subfont in candidates:
        if not path.is_file():
            continue
        try:
            if subfont is None:
                pdfmetrics.registerFont(TTFont(logical_name, str(path)))
            else:
                pdfmetrics.registerFont(TTFont(logical_name, str(path), subfontIndex=subfont))
            return logical_name
        except Exception:
            continue
    return None


def line_runs(line: str, ascii_font: str, cjk_font: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    current: list[str] = []
    current_font = ""
    for character in line:
        font = cjk_font if char_columns(character) == 2 else ascii_font
        if font != current_font and current:
            runs.append((current_font, "".join(current)))
            current = []
        current_font = font
        current.append(character)
    if current:
        runs.append((current_font, "".join(current)))
    return runs


def draw_runs(page, runs: list[tuple[str, str]], x: float, baseline: float, size: float) -> None:
    for font, text in runs:
        page.setFont(font, size)
        page.drawString(x, baseline, text)
        x += pdfmetrics.stringWidth(text, font, size)


def draw_centered(page, runs: list[tuple[str, str]], baseline: float, size: float) -> None:
    total = sum(pdfmetrics.stringWidth(text, font, size) for font, text in runs)
    draw_runs(page, runs, (PAGE_WIDTH_PT - total) / 2, baseline, size)


def parse_pages(txt_path: Path) -> list[list[str]]:
    text = txt_path.read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return [chunk.split("\n") for chunk in text.split("\n\f\n")]


def smoke_render(pdf_path: Path, render_dir: Path, page_numbers: list[int]) -> tuple[list[dict], list[str]]:
    try:
        import pymupdf
    except ImportError:
        return [], []
    rendered, issues = [], []
    render_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as document:
        for number in page_numbers:
            pixmap = document[number - 1].get_pixmap(dpi=120)
            target = render_dir / f"page-{number:02d}.png"
            pixmap.save(target)
            samples = pixmap.samples
            nonwhite = sum(1 for value in samples if value < 248)
            ratio = nonwhite / max(1, len(samples))
            if ratio < 0.0005:
                issues.append(f"疑似空白页：{number}")
            rendered.append({"page": number, "path": str(target), "sha256": sha256_file(target),
                             "nonwhite_ratio": round(ratio, 6)})
    return rendered, issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--volume", required=True, choices=["all", "front_30", "back_30"])
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--expected-pages", required=True, type=int)
    args = parser.parse_args()
    started = time.perf_counter()

    cjk = register_first("SoftCertCJK", CJK_CANDIDATES, "SOFTCERT_CODE_CJK_FONT")
    ascii_mono = register_first("SoftCertMono", ASCII_CANDIDATES, "SOFTCERT_CODE_ASCII_FONT")
    if not cjk or not ascii_mono:
        print("RENDER_UNAVAILABLE=no usable TrueType fonts (CJK monospace pair)", file=sys.stderr)
        return 4
    latin_text = register_first("SoftCertLatin", LATIN_TEXT_CANDIDATES, "SOFTCERT_CODE_LATIN_FONT") or ascii_mono

    provenance = load_json(args.provenance)
    facts = load_json(args.facts)
    groups = provenance.get("filing_groups", {})
    if args.volume not in groups:
        print(f"RENDER_ERROR=volume {args.volume} not in provenance filing groups", file=sys.stderr)
        return 2
    artifact = provenance.get("artifacts", {}).get(f"{args.volume}_txt", {})
    txt_path = Path(artifact.get("path", ""))
    if not txt_path.is_file():
        print("RENDER_ERROR=page source txt missing", file=sys.stderr)
        return 2
    if sha256_file(txt_path) != artifact.get("sha256"):
        print("RENDER_ERROR=page source txt does not match provenance hash", file=sys.stderr)
        return 2

    pages = parse_pages(txt_path)
    expected = int(args.expected_pages)
    if len(pages) != expected or groups[args.volume].get("page_count") != expected:
        print(f"RENDER_ERROR=expected {expected} pages, composed {len(pages)}", file=sys.stderr)
        return 2

    layout = provenance.get("layout", {})
    font_size = float(layout.get("font_size_pt", 10.0))
    line_height = float(layout.get("line_spacing_pt", 14.1))
    page_start = 31 if args.volume == "back_30" else 1
    filing_total = sum(int(item.get("page_count", 0)) for item in groups.values())
    header_text = f"{facts.get('software_full_name', '软件源程序')}{facts.get('version', '')}"
    header_runs = line_runs(header_text, latin_text, cjk)

    descent = max(abs(pdfmetrics.getAscentDescent(font, font_size)[1])
                  for font in (ascii_mono, cjk))
    args.pdf.parent.mkdir(parents=True, exist_ok=True)
    page = canvas.Canvas(str(args.pdf), pagesize=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT),
                         pageCompression=1, invariant=1)
    page.setTitle(f"{header_text} 源程序")
    page.setCreator("software-certificate-skill direct renderer")
    page.setFillColor(black)
    for index, lines in enumerate(pages):
        draw_centered(page, header_runs, PAGE_HEIGHT_PT - HEADER_BASELINE_FROM_TOP_PT, HEADER_FOOTER_SIZE)
        footer_runs = line_runs(f"第 {page_start + index} 页  共 {filing_total} 页", latin_text, cjk)
        draw_centered(page, footer_runs, FOOTER_BASELINE_FROM_BOTTOM_PT, HEADER_FOOTER_SIZE)
        for line_no, line in enumerate(lines):
            if not line:
                continue
            baseline = PAGE_HEIGHT_PT - MARGIN_TOP_PT - (line_no + 1) * line_height + descent
            draw_runs(page, line_runs(line, ascii_mono, cjk), MARGIN_LEFT_PT, baseline, font_size)
        page.showPage()
    page.save()

    from pypdf import PdfReader
    actual_pages = len(PdfReader(str(args.pdf)).pages)
    issues: list[str] = []
    if actual_pages != expected:
        issues.append(f"期望{expected}页，实际PDF为{actual_pages}页")
    rendered, render_issues = ([], [])
    if args.render_dir and not issues:
        rendered, render_issues = smoke_render(args.pdf, args.render_dir.resolve(),
                                               sorted({1, len(pages)}))
        issues.extend(render_issues)
    elapsed = round(time.perf_counter() - started, 3)

    report = {
        "schema_version": "1.1",
        "generated_at": now_iso(),
        "input": str(txt_path),
        "input_sha256": artifact.get("sha256"),
        "pdf": str(args.pdf.resolve()),
        "pdf_sha256": sha256_file(args.pdf),
        "pdf_pages": actual_pages,
        "expected_pages": expected,
        "rendered_pages": len(rendered),
        "conversion": {"engine": "Direct PDF (ReportLab)", "label": "direct-pdf",
                       "status": "pass" if not issues else "fail",
                       "elapsed_seconds": elapsed,
                       "fonts": {"ascii": ascii_mono, "cjk": cjk, "latin_text": latin_text},
                       "pagination": "fixed line grid, correct by construction"},
        "conversion_attempts": [{"engine": "Direct PDF (ReportLab)",
                                 "status": "pass" if not issues else "fail", "diagnostic": None}],
        "render": {"status": "pass" if rendered else "skipped", "mode": "spot_check", "diagnostic": None},
        "rendered": rendered,
        "issues": issues,
        "status": "pass" if not issues else "fail",
    }
    save_json(args.report.resolve(), report)
    print(f"PDF={args.pdf.resolve()}")
    print(f"PAGES={actual_pages} RENDERED={len(rendered)} STATUS={report['status']} ELAPSED={elapsed}s")
    print(f"REPORT={args.report.resolve()}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
