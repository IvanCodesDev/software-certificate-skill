#!/usr/bin/env python3
"""Convert DOCX to a real PDF, count pages, optionally render pages, and emit QA metadata."""

from __future__ import annotations

import argparse
import os
import socket
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import now_iso, save_json, sha256_file


def first_executable(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        path = Path(name)
        if path.is_file():
            return str(path)
    return None


def pdf_pages(path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:
        data = path.read_bytes()
        counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", data)]
        if counts:
            return max(counts)
        return len(re.findall(rb"/Type\s*/Page(?!s)", data))


def convert_with_libreoffice(source: Path, destination: Path) -> dict:
    soffice = first_executable([
        os.environ.get("SOFFICE", ""), "soffice", "libreoffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ])
    if not soffice:
        raise RuntimeError("未找到 LibreOffice/soffice；需要真实办公套件完成 PDF 转换。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    lo_python = Path(soffice).with_name("python.exe")
    if lo_python.is_file():
        return convert_with_libreoffice_uno(Path(soffice), lo_python, source, destination)
    with tempfile.TemporaryDirectory(prefix="softcert-lo-") as profile, tempfile.TemporaryDirectory(prefix="softcert-out-") as out:
        command = [
            soffice, "--headless", "--nologo", "--nodefault", "--nolockcheck", "--nofirststartwizard",
            f"-env:UserInstallation={Path(profile).as_uri()}", "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", out, str(source),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=240)
        generated = Path(out) / f"{source.stem}.pdf"
        if completed.returncode != 0 or not generated.is_file():
            raise RuntimeError(f"LibreOffice 转换失败({completed.returncode})：{completed.stderr or completed.stdout}")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(generated, temporary)
        os.replace(temporary, destination)
        return {"engine": "LibreOffice", "command": command, "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(), "exit_status": completed.returncode}


def convert_with_libreoffice_uno(soffice: Path, lo_python: Path, source: Path, destination: Path) -> dict:
    uno_script = r'''import sys, time, uno
from com.sun.star.beans import PropertyValue

def prop(name, value):
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item

input_path, output_path, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
local = uno.getComponentContext()
resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
context = None
for _ in range(60):
    try:
        context = resolver.resolve(f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
        break
    except Exception:
        time.sleep(0.5)
if context is None:
    raise RuntimeError("LibreOffice UNO listener did not become ready")
desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(input_path), "_blank", 0,
                                        (prop("Hidden", True), prop("ReadOnly", False), prop("UpdateDocMode", 3)))
if document is None:
    raise RuntimeError("LibreOffice did not open the DOCX")
try:
    indexes = document.getDocumentIndexes()
    for index in range(indexes.getCount()):
        indexes.getByIndex(index).update()
    if hasattr(document, "updateLinks"):
        document.updateLinks()
    if hasattr(document, "calculateAll"):
        document.calculateAll()
    document.storeToURL(uno.systemPathToFileUrl(output_path),
                        (prop("FilterName", "writer_pdf_Export"), prop("Overwrite", True)))
    print(f"INDEXES_UPDATED={indexes.getCount()}")
finally:
    document.close(True)
    desktop.terminate()
'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="softcert-uno-", ignore_cleanup_errors=True) as temp:
        temp_path = Path(temp)
        staged_docx, staged_pdf = temp_path / "input.docx", temp_path / "output.pdf"
        profile, script = temp_path / "profile", temp_path / "convert_uno.py"
        profile.mkdir()
        shutil.copy2(source, staged_docx)
        script.write_text(uno_script, encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        accept = f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager"
        server_command = [str(soffice), "--headless", "--nologo", "--nodefault", "--norestore", "--nolockcheck",
                          f"-env:UserInstallation={profile.as_uri()}", accept]
        server = subprocess.Popen(server_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            command = [str(lo_python), str(script), str(staged_docx), str(staged_pdf), str(port)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=240)
            if completed.returncode != 0 or not staged_pdf.is_file():
                raise RuntimeError(f"LibreOffice UNO转换失败({completed.returncode})：{completed.stderr or completed.stdout}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(staged_pdf, temporary)
            os.replace(temporary, destination)
            return {"engine": "LibreOffice UNO", "command": command[:1] + ["<temporary-script>", "<staged-docx>",
                    "<staged-pdf>", str(port)], "server_command": server_command, "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(), "exit_status": completed.returncode}
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


def convert_with_word(source: Path, destination: Path) -> dict:
    word = Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE")
    if os.name != "nt" or not word.is_file():
        raise RuntimeError("未找到可用于真实转换的 LibreOffice 或 Microsoft Word。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    script = r'''param([string]$InputDocx,[string]$OutputPdf,[string]$LogPath)
$ErrorActionPreference='Stop'
$word=$null; $doc=$null
try {
  'start' | Set-Content -LiteralPath $LogPath
  $word=New-Object -ComObject Word.Application
  'word-created' | Add-Content -LiteralPath $LogPath
  $word.Visible=$false
  $word.DisplayAlerts=0
  $word.Options.SaveNormalPrompt=$false
  $word.Options.UpdateLinksAtOpen=$false
  $doc=$word.Documents.Open($InputDocx,$false,$false,$false)
  'document-opened' | Add-Content -LiteralPath $LogPath
  foreach($toc in $doc.TablesOfContents){ $toc.Update() }
  'toc-updated' | Add-Content -LiteralPath $LogPath
  $doc.Fields.Update() | Out-Null
  'fields-updated' | Add-Content -LiteralPath $LogPath
  $doc.Repaginate()
  'repaginated' | Add-Content -LiteralPath $LogPath
  $doc.SaveAs2($OutputPdf,17)
  'pdf-saved' | Add-Content -LiteralPath $LogPath
} finally {
  if($doc){$doc.Close(0)}
  if($word){$word.Quit()}
  if($doc){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)}
  if($word){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)}
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}'''
    with tempfile.TemporaryDirectory(prefix="softcert-word-") as temp:
        ps1 = Path(temp) / "convert.ps1"
        staged_docx = Path(temp) / "input.docx"
        staged_pdf = Path(temp) / "output.pdf"
        log_path = Path(temp) / "word-progress.log"
        shutil.copy2(source, staged_docx)
        ps1.write_text(script, encoding="utf-8-sig")
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                   "-InputDocx", str(staged_docx), "-OutputPdf", str(staged_pdf)]
        command += ["-LogPath", str(log_path)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if completed.returncode != 0 or not staged_pdf.is_file():
            raise RuntimeError(f"Microsoft Word转换失败({completed.returncode})：{completed.stderr or completed.stdout}")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(staged_pdf, temporary)
        os.replace(temporary, destination)
        return {"engine": "Microsoft Word", "command": command[0:6] + ["<temporary-script>", "-InputDocx", str(source),
                "-OutputPdf", str(destination)], "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip(),
                "exit_status": completed.returncode}


def render_pdf(pdf: Path, output_dir: Path, dpi: int) -> list[Path]:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin"
    renderer = first_executable([str(bundled / "pdftoppm.exe"), str(bundled / "pdftocairo.exe"),
                                 "pdftoppm", "pdftocairo"])
    if not renderer:
        return []
    if output_dir.exists():
        for old in output_dir.glob("page-*.png"):
            old.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    command = [renderer, "-png", "-r", str(dpi), str(pdf), str(prefix)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError(f"PDF 渲染失败({completed.returncode})：{completed.stderr}")
    pages = sorted(output_dir.glob("page-*.png"))
    return pages


def inspect_rendered(images: list[Path]) -> list[dict]:
    records = []
    for index, path in enumerate(images, 1):
        record = {"page": index, "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        try:
            from PIL import Image, ImageStat
            with Image.open(path) as image:
                grayscale = image.convert("L")
                extrema = grayscale.getextrema()
                stat = ImageStat.Stat(grayscale)
                record.update({"width": image.width, "height": image.height,
                               "mean_luma": round(stat.mean[0], 2), "extrema": list(extrema),
                               "possibly_blank": extrema[0] > 248 or stat.var[0] < 1.2})
        except Exception as exc:
            record["image_inspection_error"] = str(exc)
        records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--dpi", type=int, default=120)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    source, pdf = args.input.resolve(), args.pdf.resolve()
    if source.suffix.lower() != ".docx" or not source.is_file():
        parser.error("--input 必须是存在的 DOCX 文件")
    try:
        conversion = convert_with_libreoffice(source, pdf)
    except RuntimeError as libreoffice_error:
        conversion = convert_with_word(source, pdf)
        conversion["fallback_reason"] = str(libreoffice_error)
    count = pdf_pages(pdf)
    images = [] if args.no_render else render_pdf(pdf, (args.render_dir or pdf.parent / f"{pdf.stem}-逐页渲染").resolve(), args.dpi)
    rendered = inspect_rendered(images)
    issues = []
    if count <= 0:
        issues.append("PDF页数为0")
    if images and len(images) != count:
        issues.append(f"PDF页数{count}与渲染页数{len(images)}不一致")
    if args.expected_pages is not None and count != args.expected_pages:
        issues.append(f"期望{args.expected_pages}页，实际PDF为{count}页")
    blank_pages = [item["page"] for item in rendered if item.get("possibly_blank")]
    if blank_pages:
        issues.append(f"疑似空白页：{blank_pages}")
    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "input": str(source),
        "input_sha256": sha256_file(source), "pdf": str(pdf), "pdf_sha256": sha256_file(pdf),
        "pdf_pages": count, "expected_pages": args.expected_pages, "rendered_pages": len(images),
        "conversion": conversion, "rendered": rendered, "issues": issues,
        "status": "pass" if not issues else "fail",
    }
    save_json(args.report.resolve(), report)
    print(f"PDF={pdf}")
    print(f"PAGES={count} RENDERED={len(images)} STATUS={report['status']}")
    print(f"REPORT={args.report.resolve()}")
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
