#!/usr/bin/env python3
"""Convert DOCX to PDF with bounded office processes and render diagnostics."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from common import now_iso, save_json, sha256_file, utf8_subprocess_env


class ConversionFailure(RuntimeError):
    def __init__(self, message: str, diagnostic: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic or {"status": "error", "message": message}


def first_executable(names: list[str]) -> str | None:
    for name in names:
        if not name:
            continue
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


def safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "process"


def kill_process_tree(process: subprocess.Popen[Any], extra_pids: list[int] | None = None) -> list[str]:
    actions: list[str] = []
    pids = [process.pid, *(extra_pids or [])]
    if os.name == "nt":
        for pid in dict.fromkeys(pids):
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=20,
            )
            actions.append(f"taskkill pid={pid} exit={completed.returncode}")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            actions.append(f"killpg pid={process.pid}")
        except ProcessLookupError:
            actions.append(f"killpg pid={process.pid} already-exited")
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        actions.append(f"direct-kill pid={process.pid}")
    return actions


def progress_pids(path: Path | None) -> list[int]:
    if path is None or not path.is_file():
        return []
    values = re.findall(r"(?:word-pid|child-pid)=(\d+)", path.read_text(encoding="utf-8-sig", errors="replace"))
    return [int(value) for value in values]


def run_isolated(command: list[str], timeout: float, diagnostics_dir: Path, label: str,
                 env: dict[str, str] | None = None, progress_log: Path | None = None) -> dict[str, Any]:
    """Run one external process in its own group and always emit a diagnostic JSON."""
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_label(label)
    stdout_path = diagnostics_dir / f"{stem}.stdout.log"
    stderr_path = diagnostics_dir / f"{stem}.stderr.log"
    diagnostic_path = diagnostics_dir / f"{stem}.diagnostic.json"
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command, stdout=stdout, stderr=stderr, env=env or utf8_subprocess_env(),
                creationflags=creationflags, start_new_session=os.name != "nt",
            )
        except OSError as exc:
            diagnostic = {
                "label": label, "command": command, "pid": None,
                "started_at": now_iso(), "timeout_seconds": timeout,
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                "progress_log": str(progress_log) if progress_log else None,
                "status": "launch_error", "exit_status": None, "error": str(exc),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            save_json(diagnostic_path, diagnostic)
            raise ConversionFailure(f"{label}启动失败：{exc}", diagnostic) from exc
        diagnostic: dict[str, Any] = {
            "label": label, "command": command, "pid": process.pid,
            "started_at": now_iso(), "timeout_seconds": timeout,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
            "progress_log": str(progress_log) if progress_log else None,
        }
        try:
            process.wait(timeout=max(0.1, timeout))
            diagnostic.update({"status": "pass" if process.returncode == 0 else "error",
                               "exit_status": process.returncode})
        except subprocess.TimeoutExpired:
            diagnostic["cleanup"] = kill_process_tree(process, progress_pids(progress_log))
            diagnostic.update({"status": "timeout", "exit_status": None})
        diagnostic["elapsed_seconds"] = round(time.monotonic() - started, 3)
    diagnostic["stdout_tail"] = stdout_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    diagnostic["stderr_tail"] = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    if progress_log and progress_log.is_file():
        diagnostic["progress"] = progress_log.read_text(encoding="utf-8-sig", errors="replace")[-4000:]
    save_json(diagnostic_path, diagnostic)
    diagnostic["diagnostic"] = str(diagnostic_path)
    if diagnostic["status"] == "timeout":
        raise ConversionFailure(f"{label}超过单文档剩余时限{timeout:.1f}秒", diagnostic)
    if diagnostic["exit_status"] != 0:
        raise ConversionFailure(
            f"{label}失败({diagnostic['exit_status']})：{diagnostic['stderr_tail'] or diagnostic['stdout_tail']}",
            diagnostic,
        )
    return diagnostic


def copy_verified(source: Path, destination: Path) -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise ConversionFailure(f"转换进程未生成有效PDF：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def convert_with_libreoffice(source: Path, destination: Path, timeout: float,
                             diagnostics_dir: Path) -> dict[str, Any]:
    soffice = first_executable([
        os.environ.get("SOFFICE", ""), "soffice", "libreoffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ])
    if not soffice:
        raise ConversionFailure("未找到LibreOffice/soffice", {"engine": "LibreOffice", "status": "unavailable"})
    lo_python = Path(soffice).with_name("python.exe")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="softcert-lo-", ignore_cleanup_errors=True) as profile, \
                tempfile.TemporaryDirectory(prefix="softcert-out-", ignore_cleanup_errors=True) as out:
            command = [
                soffice, "--headless", "--nologo", "--nodefault", "--norestore", "--nolockcheck",
                "--nofirststartwizard", f"-env:UserInstallation={Path(profile).as_uri()}",
                "--convert-to", "pdf:writer_pdf_Export", "--outdir", out, str(source),
            ]
            diagnostic = run_isolated(command, timeout, diagnostics_dir, "libreoffice-cli")
            generated = Path(out) / f"{source.stem}.pdf"
            copy_verified(generated, destination)
            return {"engine": "LibreOffice CLI", **diagnostic}
    except ConversionFailure as cli_error:
        # The direct filter is faster and avoids the UNO index-update hang seen
        # on large manuals. UNO remains a bounded fallback for installations
        # where direct conversion is unavailable.
        remaining = timeout - (time.monotonic() - started)
        if not lo_python.is_file() or remaining <= 1:
            raise
        result = convert_with_libreoffice_uno(
            Path(soffice), lo_python, source, destination, remaining, diagnostics_dir)
        result["cli_failure"] = cli_error.diagnostic
        return result


def convert_with_libreoffice_uno(soffice: Path, lo_python: Path, source: Path, destination: Path,
                                  timeout: float, diagnostics_dir: Path) -> dict[str, Any]:
    uno_script = r'''import sys, time, uno
from com.sun.star.beans import PropertyValue

def prop(name, value):
    item = PropertyValue(); item.Name = name; item.Value = value; return item

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
    for index in range(indexes.getCount()): indexes.getByIndex(index).update()
    if hasattr(document, "updateLinks"): document.updateLinks()
    if hasattr(document, "calculateAll"): document.calculateAll()
    document.storeToURL(uno.systemPathToFileUrl(output_path),
        (prop("FilterName", "writer_pdf_Export"), prop("Overwrite", True)))
    print(f"INDEXES_UPDATED={indexes.getCount()}")
finally:
    document.close(True)
    desktop.terminate()
'''
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
        server_command = [str(soffice), "--headless", "--nologo", "--nodefault", "--norestore",
                          "--nolockcheck", f"-env:UserInstallation={profile.as_uri()}", accept]
        server_stdout = diagnostics_dir / "libreoffice-uno-server.stdout.log"
        server_stderr = diagnostics_dir / "libreoffice-uno-server.stderr.log"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with server_stdout.open("wb") as out, server_stderr.open("wb") as err:
            server = subprocess.Popen(server_command, stdout=out, stderr=err,
                                      creationflags=creationflags, start_new_session=os.name != "nt")
            try:
                command = [str(lo_python), str(script), str(staged_docx), str(staged_pdf), str(port)]
                diagnostic = run_isolated(command, timeout, diagnostics_dir, "libreoffice-uno-client")
                copy_verified(staged_pdf, destination)
            finally:
                if server.poll() is None:
                    cleanup = kill_process_tree(server)
                else:
                    cleanup = [f"server-exit={server.returncode}"]
                save_json(diagnostics_dir / "libreoffice-uno-server.diagnostic.json", {
                    "engine": "LibreOffice UNO server", "pid": server.pid,
                    "command": server_command, "cleanup": cleanup,
                    "stdout_log": str(server_stdout), "stderr_log": str(server_stderr),
                })
        return {"engine": "LibreOffice UNO", **diagnostic,
                "server_diagnostic": str(diagnostics_dir / "libreoffice-uno-server.diagnostic.json")}


def convert_with_word(source: Path, destination: Path, timeout: float,
                      diagnostics_dir: Path) -> dict[str, Any]:
    word = Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE")
    if os.name != "nt" or not word.is_file():
        raise ConversionFailure("未找到Microsoft Word", {"engine": "Microsoft Word", "status": "unavailable"})
    script = r'''param([string]$InputDocx,[string]$OutputPdf,[string]$LogPath)
$ErrorActionPreference='Stop'
$word=$null; $doc=$null
$before=@(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
try {
  'start' | Set-Content -LiteralPath $LogPath -Encoding UTF8
  $word=New-Object -ComObject Word.Application
  Start-Sleep -Milliseconds 250
  @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object {$before -notcontains $_.Id}) |
    ForEach-Object {('word-pid='+$_.Id) | Add-Content -LiteralPath $LogPath -Encoding UTF8}
  'word-created' | Add-Content -LiteralPath $LogPath -Encoding UTF8
  $word.Visible=$false; $word.DisplayAlerts=0
  $word.Options.SaveNormalPrompt=$false; $word.Options.UpdateLinksAtOpen=$false
  $doc=$word.Documents.Open($InputDocx,$false,$false,$false)
  'document-opened' | Add-Content -LiteralPath $LogPath -Encoding UTF8
  foreach($toc in $doc.TablesOfContents){$toc.Update()}
  'toc-updated' | Add-Content -LiteralPath $LogPath -Encoding UTF8
  $doc.Fields.Update() | Out-Null
  'fields-updated' | Add-Content -LiteralPath $LogPath -Encoding UTF8
  $doc.Repaginate(); 'repaginated' | Add-Content -LiteralPath $LogPath -Encoding UTF8
  $doc.SaveAs2($OutputPdf,17); 'pdf-saved' | Add-Content -LiteralPath $LogPath -Encoding UTF8
} finally {
  if($doc){$doc.Close(0)}
  if($word){$word.Quit()}
  if($doc){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)}
  if($word){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)}
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}'''
    with tempfile.TemporaryDirectory(prefix="softcert-word-", ignore_cleanup_errors=True) as temp:
        temp_path = Path(temp)
        ps1, staged_docx = temp_path / "convert.ps1", temp_path / "input.docx"
        staged_pdf, progress_log = temp_path / "output.pdf", temp_path / "word-progress.log"
        shutil.copy2(source, staged_docx)
        ps1.write_text(script, encoding="utf-8-sig")
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(ps1), "-InputDocx", str(staged_docx), "-OutputPdf", str(staged_pdf),
                   "-LogPath", str(progress_log)]
        diagnostic = run_isolated(command, timeout, diagnostics_dir, "microsoft-word", progress_log=progress_log)
        copy_verified(staged_pdf, destination)
        return {"engine": "Microsoft Word", **diagnostic}


def render_pdf(pdf: Path, output_dir: Path, dpi: int, timeout: float,
               diagnostics_dir: Path) -> list[Path]:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin"
    renderer = first_executable([str(bundled / "pdftoppm.exe"), str(bundled / "pdftocairo.exe"),
                                 "pdftoppm", "pdftocairo"])
    if not renderer:
        raise ConversionFailure("未找到PDF逐页渲染器（pdftoppm/pdftocairo）",
                                {"engine": "Poppler", "status": "unavailable"})
    if output_dir.exists():
        for old in output_dir.glob("page-*.png"):
            old.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    run_isolated([renderer, "-png", "-r", str(dpi), str(pdf), str(prefix)],
                 timeout, diagnostics_dir, "pdf-render")
    return sorted(output_dir.glob("page-*.png"))


def inspect_rendered(images: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, path in enumerate(images, 1):
        record: dict[str, Any] = {"page": index, "path": str(path), "sha256": sha256_file(path),
                                  "bytes": path.stat().st_size}
        try:
            from PIL import Image, ImageStat
            with Image.open(path) as image:
                grayscale = image.convert("L")
                extrema = grayscale.getextrema()
                stat = ImageStat.Stat(grayscale)
                histogram = grayscale.histogram()
                pixel_count = max(1, sum(histogram))
                dark_ratio = sum(histogram[:220]) / pixel_count
                mean_luma = stat.mean[0]
                # Headers, footers and a page number make a visually blank page
                # non-uniform, so extrema/variance alone miss it.  The added
                # high-luma + very-low-ink rule catches those pages while leaving
                # sparse covers and short legitimate sections untouched.
                possibly_blank = (
                    extrema[0] > 248
                    or stat.var[0] < 1.2
                    or (mean_luma > 254.8 and dark_ratio < 0.0015)
                )
                record.update({"width": image.width, "height": image.height,
                               "mean_luma": round(mean_luma, 2), "extrema": list(extrema),
                               "dark_pixel_ratio": round(dark_ratio, 6),
                               "possibly_blank": possibly_blank})
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
    parser.add_argument("--timeout-seconds", type=float, default=180,
                        help="total office-conversion deadline for one document")
    parser.add_argument("--render-timeout-seconds", type=float, default=180)
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    source, pdf, report_path = args.input.resolve(), args.pdf.resolve(), args.report.resolve()
    if source.suffix.lower() != ".docx" or not source.is_file():
        parser.error("--input必须是存在的DOCX文件")
    diagnostics_dir = (args.diagnostics_dir or report_path.parent / f"{report_path.stem}-diagnostics").resolve()
    if pdf.exists():
        pdf.unlink()
    deadline = time.monotonic() + max(1.0, args.timeout_seconds)
    attempts: list[dict[str, Any]] = []

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0.1:
            raise ConversionFailure("单文档转换总时限已耗尽", {"status": "timeout"})
        return value

    conversion: dict[str, Any] | None = None
    for engine, converter in (("LibreOffice", convert_with_libreoffice), ("Microsoft Word", convert_with_word)):
        try:
            conversion = converter(source, pdf, remaining(), diagnostics_dir)
            attempts.append({"engine": engine, "status": "pass", "diagnostic": conversion.get("diagnostic")})
            break
        except ConversionFailure as exc:
            attempts.append({"engine": engine, "status": exc.diagnostic.get("status", "error"),
                             "message": str(exc), "diagnostic": exc.diagnostic})
    if conversion is None:
        failure = {
            "schema_version": "1.1", "generated_at": now_iso(), "input": str(source),
            "input_sha256": sha256_file(source), "pdf": str(pdf), "pdf_pages": 0,
            "conversion_attempts": attempts, "diagnostics_dir": str(diagnostics_dir),
            "issues": ["办公套件转换失败或超时"], "status": "fail",
        }
        save_json(report_path, failure)
        print(f"PDF={pdf}")
        print("PAGES=0 RENDERED=0 STATUS=fail")
        print(f"REPORT={report_path}")
        return 4
    count = pdf_pages(pdf)
    render_diagnostic: dict[str, Any] | None = None
    try:
        images = [] if args.no_render else render_pdf(
            pdf, (args.render_dir or pdf.parent / f"{pdf.stem}-逐页渲染").resolve(),
            args.dpi, args.render_timeout_seconds, diagnostics_dir,
        )
    except ConversionFailure as exc:
        images = []
        render_diagnostic = exc.diagnostic
    rendered = inspect_rendered(images)
    issues: list[str] = []
    if count <= 0:
        issues.append("PDF页数为0")
    if render_diagnostic:
        issues.append(f"PDF逐页渲染失败：{render_diagnostic.get('status', 'error')}")
    if not args.no_render and len(images) != count:
        issues.append(f"PDF页数{count}与渲染页数{len(images)}不一致")
    if args.expected_pages is not None and count != args.expected_pages:
        issues.append(f"期望{args.expected_pages}页，实际PDF为{count}页")
    blank_pages = [item["page"] for item in rendered if item.get("possibly_blank")]
    if blank_pages:
        issues.append(f"疑似空白页：{blank_pages}")
    report = {
        "schema_version": "1.1", "generated_at": now_iso(), "input": str(source),
        "input_sha256": sha256_file(source), "pdf": str(pdf), "pdf_sha256": sha256_file(pdf),
        "pdf_pages": count, "expected_pages": args.expected_pages, "rendered_pages": len(images),
        "conversion": conversion, "conversion_attempts": attempts,
        "render": {"status": "skipped" if args.no_render else ("fail" if render_diagnostic else "pass"),
                   "diagnostic": render_diagnostic},
        "diagnostics_dir": str(diagnostics_dir), "rendered": rendered, "issues": issues,
        "status": "pass" if not issues else "fail",
    }
    save_json(report_path, report)
    print(f"PDF={pdf}")
    print(f"PAGES={count} RENDERED={len(images)} STATUS={report['status']}")
    print(f"REPORT={report_path}")
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
