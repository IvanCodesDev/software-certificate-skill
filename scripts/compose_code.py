#!/usr/bin/env python3
"""Compose provenance-traceable source identification materials."""

from __future__ import annotations

import argparse
import fnmatch
import math
import textwrap
from pathlib import Path

from common import load_json, now_iso, relative_posix, safe_text, save_json, sha256_file

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt
except ImportError as exc:
    raise SystemExit("python-docx is required; use the bundled document runtime.") from exc


DEFAULT_EXTENSIONS = {
    ".py", ".cs", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".php", ".rb", ".swift", ".sql",
    ".cshtml", ".razor"
}
SENSITIVE_TOKENS = ("privatekey", "private_key", "id_rsa", "id_dsa", "secret.key", ".env")


def matches_any(relative: str, patterns: list[str]) -> bool:
    normalized = relative.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) or Path(normalized).match(pattern) for pattern in patterns)


def select_files(root: Path, manifest: dict) -> list[Path]:
    ordered = manifest.get("ordered_files", [])
    exclude = list(manifest.get("exclude", []))
    if ordered:
        candidates = [(root / item).resolve() for item in ordered]
    else:
        candidates_set: set[Path] = set()
        for pattern in manifest.get("include", ["**/*"]):
            candidates_set.update(path.resolve() for path in root.glob(pattern) if path.is_file())
        candidates = sorted(candidates_set, key=lambda p: relative_posix(p, root).lower())
    result: list[Path] = []
    for path in candidates:
        try:
            rel = relative_posix(path, root)
        except ValueError as exc:
            raise ValueError(f"Manifest path escapes project root: {path}") from exc
        if not path.is_file() or path.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        if matches_any(rel, exclude):
            continue
        if any(token in rel.lower() for token in SENSITIVE_TOKENS):
            continue
        result.append(path)
    return result


def wrap_source_line(line: str, max_chars: int) -> list[str]:
    expanded = line.expandtabs(4).rstrip()
    if not expanded:
        return [""]
    return textwrap.wrap(expanded, width=max_chars, replace_whitespace=False,
                         drop_whitespace=False, break_long_words=True,
                         break_on_hyphens=False) or [""]


def collect_lines(root: Path, files: list[Path], max_chars: int) -> tuple[list[str], list[dict], list[dict]]:
    output: list[str] = []
    mapping: list[dict] = []
    file_records: list[dict] = []
    for path in files:
        text = safe_text(path, max_bytes=20_000_000)
        if text is None:
            continue
        rel = relative_posix(path, root)
        digest = sha256_file(path)
        start = len(output) + 1
        original_lines = text.splitlines()
        for original_no, original in enumerate(original_lines, 1):
            wrapped = wrap_source_line(original, max_chars)
            for segment_no, segment in enumerate(wrapped, 1):
                output.append(segment)
                mapping.append({
                    "output_line": len(output),
                    "file": rel,
                    "file_sha256": digest,
                    "source_line": original_no,
                    "segment": segment_no
                })
        file_records.append({
            "path": rel,
            "sha256": digest,
            "original_lines": len(original_lines),
            "output_start_line": start,
            "output_end_line": len(output)
        })
    return output, mapping, file_records


def chunked(lines: list[str], per_page: int) -> list[list[str]]:
    return [lines[index:index + per_page] for index in range(0, len(lines), per_page)]


def write_txt(path: Path, pages: list[list[str]]) -> None:
    path.write_text("\n\f\n".join("\n".join(page) for page in pages) + "\n", encoding="utf-8")


def add_page_field(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, text, end):
        run._r.append(node)


def write_docx(path: Path, pages: list[list[str]], source_page_numbers: list[int], facts: dict, label: str,
               font_size: float = 9.0, line_spacing: float = 10.8) -> None:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(22)
    section.bottom_margin = Mm(25.4)
    section.left_margin = Mm(20)
    section.right_margin = Mm(20)
    section.header_distance = Mm(15)
    section.footer_distance = Mm(17.5)
    normal = doc.styles["Normal"]
    normal.font.name = "Courier New"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    normal.font.size = Pt(font_size)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    normal.paragraph_format.line_spacing = Pt(line_spacing)
    normal.paragraph_format.widow_control = False

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header.add_run(f"{facts.get('software_full_name', '软件源程序')}{facts.get('version', '')}")
    for run in header.runs:
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
        run.font.size = Pt(9)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("第 ")
    add_page_field(footer)
    footer.add_run(" 页")
    for run in footer.runs:
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
        run.font.size = Pt(9)

    for page_index, lines in enumerate(pages):
        if page_index:
            doc.add_page_break()
        for line in lines:
            paragraph = doc.add_paragraph()
            paragraph.add_run(line if line else " ")
    doc.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--lines-per-page", type=int, default=50)
    parser.add_argument("--max-chars", type=int, default=88)
    parser.add_argument("--font-size", type=float, default=8.5)
    parser.add_argument("--line-spacing", type=float, default=10.0)
    args = parser.parse_args()
    if args.lines_per_page < 50:
        parser.error("--lines-per-page must be at least 50")
    root = args.project.resolve()
    manifest = load_json(args.manifest)
    facts = load_json(args.facts) if args.facts else {}
    files = select_files(root, manifest)
    if not files:
        parser.error("No eligible source files selected by the manifest")
    lines, mapping, file_records = collect_lines(root, files, args.max_chars)
    if not lines:
        parser.error("Selected files contain no readable source lines")
    full_pages = chunked(lines, args.lines_per_page)
    total_pages = len(full_pages)
    if total_pages < 60:
        groups = {"all": list(range(total_pages))}
        selection = "all_under_60_pages"
    else:
        groups = {"front_30": list(range(30)), "back_30": list(range(total_pages - 30, total_pages))}
        selection = "first_30_and_last_30_separate_volumes"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    full_txt = output / "source-full.txt"
    full_docx = output / "source-full.docx"
    write_txt(full_txt, full_pages)
    write_docx(full_docx, full_pages, list(range(1, total_pages + 1)), facts, "完整归档版",
               args.font_size, args.line_spacing)
    artifacts = {
        "full_txt": {"path": str(full_txt), "sha256": sha256_file(full_txt)},
        "full_docx": {"path": str(full_docx), "sha256": sha256_file(full_docx)},
    }
    filing_groups = {}
    for key, indices in groups.items():
        pages = [full_pages[index] for index in indices]
        source_numbers = [index + 1 for index in indices]
        txt_path = output / f"source-{key.replace('_', '-')}.txt"
        docx_path = output / f"source-{key.replace('_', '-')}.docx"
        write_txt(txt_path, pages)
        write_docx(docx_path, pages, source_numbers, facts,
                   "全部源程序" if key == "all" else ("前30页" if key == "front_30" else "后30页"),
                   args.font_size, args.line_spacing)
        artifacts[f"{key}_txt"] = {"path": str(txt_path), "sha256": sha256_file(txt_path)}
        artifacts[f"{key}_docx"] = {"path": str(docx_path), "sha256": sha256_file(docx_path)}
        filing_groups[key] = {
            "logical_source_pages": source_numbers,
            "page_count": len(pages),
            "first_output_line": indices[0] * args.lines_per_page + 1 if indices else None,
            "last_output_line": indices[-1] * args.lines_per_page + len(pages[-1]) if indices else None,
        }

    line_pages = []
    for page_no, page_lines in enumerate(full_pages, 1):
        start = (page_no - 1) * args.lines_per_page + 1
        line_pages.append({
            "page": page_no,
            "start_output_line": start,
            "end_output_line": start + len(page_lines) - 1,
            "line_count": len(page_lines)
        })
    provenance = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "project_root": str(root),
        "manifest": str(args.manifest.resolve()),
        "manifest_review": manifest.get("review", {}),
        "ordered_files_confirmed": bool(manifest.get("ordered_files")),
        "selection_policy": manifest.get("selection_policy", {}),
        "file_decisions": manifest.get("file_decisions", []),
        "lines_per_page": args.lines_per_page,
        "max_chars": args.max_chars,
        "original_line_count": sum(item.get("original_lines", 0) for item in file_records),
        "full_line_count": len(lines),
        "full_page_count": total_pages,
        "filing_page_count": sum(item["page_count"] for item in filing_groups.values()),
        "selection": selection,
        "filing_groups": filing_groups,
        "files": file_records,
        "pages": line_pages,
        "line_mapping": mapping,
        "layout": {"font_size_pt": args.font_size, "line_spacing_pt": args.line_spacing},
        "artifacts": artifacts,
    }
    provenance_path = output / "source-provenance.json"
    save_json(provenance_path, provenance)
    print(f"SOURCE_OUTPUT={output}")
    print(f"FILES={len(file_records)} LINES={len(lines)} FULL_PAGES={total_pages} FILING_PAGES={sum(item['page_count'] for item in filing_groups.values())} SELECTION={selection}")
    print(f"PROVENANCE={provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
