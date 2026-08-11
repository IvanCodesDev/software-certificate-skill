#!/usr/bin/env python3
"""Install software-certificate-skill for supported agent platforms."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

SKILL_NAME = "software-certificate-skill"
PLATFORMS = ("codex", "claude-code", "cursor", "opencode", "workbuddy", "qoderwork", "traework")
NATIVE_PROJECT = {
    "claude-code": Path(".claude/skills") / SKILL_NAME,
    "cursor": Path(".cursor/skills") / SKILL_NAME,
    "opencode": Path(".opencode/skills") / SKILL_NAME,
}
NATIVE_USER = {
    "codex": Path(".codex/skills") / SKILL_NAME,
    "claude-code": Path(".claude/skills") / SKILL_NAME,
    "cursor": Path(".cursor/skills") / SKILL_NAME,
    "opencode": Path(".config/opencode/skills") / SKILL_NAME,
}


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def ignore_copy(_directory: str, names: list[str]) -> set[str]:
    ignored = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "case", "cases", "output", "outputs"}
    return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo", ".tmp", ".log"))}


def copy_skill(source: Path, destination: Path, force: bool, dry_run: bool) -> dict[str, Any]:
    if is_relative_to(destination, source):
        raise ValueError(f"destination must be outside the source skill: {destination}")
    action = "replace" if destination.exists() else "create"
    if destination.exists() and not force:
        return {"path": str(destination), "action": "skip_existing", "status": "skipped"}
    if dry_run:
        return {"path": str(destination), "action": action, "status": "planned"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=str(destination.parent)))
    staged_skill = stage / SKILL_NAME
    try:
        shutil.copytree(source, staged_skill, ignore=ignore_copy)
        if not (staged_skill / "SKILL.md").is_file():
            raise RuntimeError("staged installation lacks SKILL.md")
        if destination.exists():
            backup = destination.with_name(destination.name + ".previous")
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(destination, backup)
            try:
                os.replace(staged_skill, destination)
            except Exception:
                os.replace(backup, destination)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(staged_skill, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"path": str(destination), "action": action, "status": "installed"}


def managed_block(skill_path: Path) -> str:
    resolved = skill_path.resolve().as_posix()
    return (
        "<!-- software-certificate-skill:start -->\n"
        "## Software Certificate Skill\n\n"
        f"When a task involves 软件著作权、软著材料、操作手册、源程序材料或自动截图，"
        f"read and follow `{resolved}/SKILL.md`. Resolve every referenced script, asset and reference "
        f"relative to `{resolved}`. Use the bundled deterministic scripts instead of recreating them.\n"
        "<!-- software-certificate-skill:end -->"
    )


def upsert_block(path: Path, block: str, dry_run: bool) -> dict[str, Any]:
    start = "<!-- software-certificate-skill:start -->"
    end = "<!-- software-certificate-skill:end -->"
    current = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if start in current and end in current:
        before = current.split(start, 1)[0].rstrip()
        after = current.split(end, 1)[1].lstrip()
        updated = "\n\n".join(part for part in (before, block, after) if part).rstrip() + "\n"
        action = "update"
    else:
        updated = (current.rstrip() + "\n\n" + block + "\n").lstrip()
        action = "append" if current else "create"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
    return {"path": str(path), "action": action, "status": "planned" if dry_run else "installed"}


def platform_rule_path(platform: str, project: Path) -> Path | None:
    if platform == "qoderwork":
        return project / ".qoder/rules/software-certificate-skill.md"
    if platform == "traework":
        return project / ".trae/rules/software-certificate-skill.md"
    return None


def install(source: Path, platforms: list[str], scope: str, project: Path | None,
            home: Path, force: bool, dry_run: bool) -> dict[str, Any]:
    source = source.resolve()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found under {source}")
    base = project.resolve() if project else home.resolve()
    if scope == "project" and project is None:
        raise ValueError("--project is required for project scope")
    results: list[dict[str, Any]] = []
    portable = base / ".agents/skills" / SKILL_NAME

    for platform in platforms:
        if scope == "project" and platform in NATIVE_PROJECT:
            destination = base / NATIVE_PROJECT[platform]
        elif scope == "user" and platform in NATIVE_USER:
            destination = base / NATIVE_USER[platform]
        else:
            destination = portable
        if not any(item.get("path") == str(destination) for item in results):
            result = copy_skill(source, destination, force=force, dry_run=dry_run)
            result["platform"] = platform
            native = ((scope == "project" and platform in NATIVE_PROJECT) or
                      (scope == "user" and platform in NATIVE_USER))
            result["kind"] = "native_skill" if native else "portable_skill"
            results.append(result)

        if scope == "project" and platform in {"codex", "workbuddy", "qoderwork", "traework"}:
            block = managed_block(portable)
            agents_result = upsert_block(base / "AGENTS.md", block, dry_run=dry_run)
            agents_result.update({"platform": platform, "kind": "agents_instruction"})
            results.append(agents_result)
            rule_path = platform_rule_path(platform, base)
            if rule_path:
                rule_result = upsert_block(rule_path, block, dry_run=dry_run)
                rule_result.update({"platform": platform, "kind": "platform_rule"})
                results.append(rule_result)

    return {
        "schema_version": "1.0", "source": str(source), "scope": scope,
        "platforms": platforms, "base": str(base), "dry_run": dry_run,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--platform", nargs="+", default=["all"],
                        choices=[*PLATFORMS, "all"])
    parser.add_argument("--scope", choices=["project", "user"], default="project")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    platforms = list(PLATFORMS) if "all" in args.platform else list(dict.fromkeys(args.platform))
    report = install(args.source, platforms, args.scope, args.project, args.home,
                     force=args.force, dry_run=args.dry_run)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
