#!/usr/bin/env python3
"""Shared product-workflow paths, state, hashing, backups, and reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common import load_json, now_iso, save_json, sha256_file, sha256_text


STAGES = (
    "environment", "project_analysis", "business_understanding", "intake",
    "application_form", "source_selection", "screenshots", "manual",
    "code_material", "render", "verification", "release",
)


@dataclass(frozen=True)
class ProductPaths:
    project: Path
    root: Path
    formal: Path
    runtime: Path
    draft: Path
    quality: Path
    screenshots: Path
    work: Path
    state: Path
    history: Path

    @classmethod
    def create(cls, project: Path, output: Path | None = None) -> "ProductPaths":
        project = project.resolve()
        root = (output or project / "软件著作权申请资料").resolve()
        runtime_base = Path(os.environ.get(
            "SOFTWARE_CERTIFICATE_RUNTIME_ROOT",
            str(Path(tempfile.gettempdir()) / "software-certificate-skill"),
        )).resolve()
        runtime_key = sha256_text(f"{str(project).casefold()}\n{str(root).casefold()}")[:20]
        runtime = runtime_base / runtime_key
        value = cls(
            project=project, root=root, formal=root / "正式资料", runtime=runtime,
            draft=runtime / "draft", quality=runtime / "quality",
            screenshots=runtime / "user-screenshots", work=runtime / "work",
            state=runtime / "workflow-state.json", history=runtime / "history",
        )
        # Only the formal delivery directory is created in the project.  Every
        # resumable state, screenshot copy, report, render and backup lives in
        # the operating-system runtime area and never pollutes user delivery.
        for path in (value.formal, value.runtime, value.draft, value.quality,
                     value.screenshots, value.work, value.history):
            path.mkdir(parents=True, exist_ok=True)
        return value


def prune_delivery_root(paths: ProductPaths) -> None:
    """Leave only ``正式资料`` in the user-visible delivery root."""
    root = paths.root.resolve()
    formal = paths.formal.resolve()
    project = paths.project.resolve()
    if root == project or root == Path(root.anchor) or formal.parent != root:
        raise ValueError(f"unsafe delivery root: {root}")
    for child in root.iterdir():
        resolved = child.resolve()
        if resolved == formal:
            continue
        if resolved.parent != root:
            raise ValueError(f"unsafe delivery child: {resolved}")
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def safe_filename(value: str, fallback: str = "软件") -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", str(value)).strip(" .")
    return text[:120] or fallback


def hash_inputs(paths: Iterable[Path], extra: Any = None) -> str:
    records: list[tuple[str, str]] = []
    for path in paths:
        resolved = path.resolve()
        if resolved.is_file():
            records.append((str(resolved), sha256_file(resolved)))
        elif resolved.is_dir():
            for item in sorted((p for p in resolved.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
                if any(part in {".git", "node_modules", "__pycache__", "软件著作权申请资料", ".工作区", "正式资料", "质量检查"}
                       for part in item.parts):
                    continue
                records.append((str(item.relative_to(resolved)), sha256_file(item)))
    return sha256_text(json.dumps({"files": records, "extra": extra}, ensure_ascii=False, sort_keys=True))


def initial_state(paths: ProductPaths) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "created_at": now_iso(), "updated_at": now_iso(),
        "project_root": str(paths.project), "output_root": str(paths.root),
        "runtime_root": str(paths.runtime),
        "active_release": None, "previous_release": None,
        "stages": {name: {"status": "pending", "input_sha256": None, "outputs": []}
                   for name in STAGES},
        "events": [], "manual_edits": {},
    }


def load_state(paths: ProductPaths) -> dict[str, Any]:
    state = load_json(paths.state) if paths.state.exists() else initial_state(paths)
    for name in STAGES:
        state.setdefault("stages", {}).setdefault(
            name, {"status": "pending", "input_sha256": None, "outputs": []}
        )
    return state


def record_stage(paths: ProductPaths, state: dict[str, Any], name: str, status: str,
                 input_sha256: str, outputs: list[Path], message: str = "") -> None:
    state["stages"][name] = {
        "status": status, "input_sha256": input_sha256, "completed_at": now_iso(),
        "outputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in outputs if path.is_file()
        ], "message": message,
    }
    state["updated_at"] = now_iso()
    state.setdefault("events", []).append({"at": now_iso(), "stage": name, "status": status, "message": message})
    state["events"] = state["events"][-200:]
    save_json(paths.state, state)


def stage_is_current(state: dict[str, Any], name: str, input_sha256: str) -> bool:
    stage = state.get("stages", {}).get(name, {})
    if stage.get("status") != "complete" or stage.get("input_sha256") != input_sha256:
        return False
    return all(Path(item["path"]).is_file() and sha256_file(Path(item["path"])) == item.get("sha256")
               for item in stage.get("outputs", []))


def snapshot_files(paths: ProductPaths, files: Iterable[Path], reason: str) -> Path | None:
    existing = [path for path in files if path.is_file()]
    if not existing:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = paths.history / stamp
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for source in existing:
        target = destination / source.name
        shutil.copy2(source, target)
        records.append({"source": str(source), "backup": str(target), "sha256": sha256_file(target)})
    save_json(destination / "snapshot.json", {"created_at": now_iso(), "reason": reason, "files": records})
    return destination


def copy_changed(source: Path, destination: Path, paths: ProductPaths, reason: str) -> bool:
    if destination.is_file() and sha256_file(destination) == sha256_file(source):
        return False
    if destination.is_file():
        snapshot_files(paths, [destination], reason)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    return True


def find_slots(value: Any) -> list[str]:
    return sorted(set(re.findall(r"【[^】]*(?:待确认|待填写|待补充|待申请人确认)[^】]*】",
                                 json.dumps(value, ensure_ascii=False))))


def file_manifest(root: Path, excluded: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = excluded or set()
    records = []
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        if path.name in excluded or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        records.append({
            "path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return records


def write_sha256s(root: Path, output: Path) -> None:
    lines = [f"{item['sha256']}  {item['path']}" for item in file_manifest(root, {output.name})]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
