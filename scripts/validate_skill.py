#!/usr/bin/env python3
"""Run platform-independent structural validation for this skill."""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_PATTERN = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:|#)([^)]+)\)")


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md frontmatter is not closed")
    metadata: dict[str, str] = {}
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, parts[2]


def check_links(path: Path, root: Path) -> list[str]:
    missing: list[str] = []
    text = path.read_text(encoding="utf-8-sig")
    for match in LINK_PATTERN.finditer(text):
        raw = unquote(match.group(1).split("#", 1)[0])
        if raw and not (path.parent / raw).resolve().exists():
            missing.append(f"{path.relative_to(root).as_posix()} -> {match.group(1)}")
    return missing


def validate(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        errors.append("SKILL.md is missing")
        return {"status": "fail", "errors": errors, "warnings": warnings}
    try:
        metadata, body = parse_frontmatter(skill_path)
        if set(metadata) != {"name", "description"}:
            errors.append("frontmatter must contain exactly name and description")
        name = metadata.get("name", "")
        if not NAME_PATTERN.match(name):
            errors.append("skill name must use lowercase letters, digits and hyphens")
        if root.name != name:
            errors.append(f"folder name {root.name!r} differs from skill name {name!r}")
        if len(metadata.get("description", "")) < 40:
            errors.append("description is too short to trigger reliably")
        if len(body.splitlines()) > 500:
            warnings.append("SKILL.md exceeds 500 lines; move detail into references")
    except ValueError as exc:
        errors.append(str(exc))

    for required in ("scripts", "references", "assets"):
        if not (root / required).is_dir():
            errors.append(f"required directory is missing: {required}")
    for document in (root / "SKILL.md", root / "README.md"):
        if document.is_file():
            errors.extend(f"broken local link: {value}" for value in check_links(document, root))
    json_files = list((root / "assets").rglob("*.json")) if (root / "assets").exists() else []
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append(f"invalid JSON {path.relative_to(root).as_posix()}: {exc}")
    scripts = list((root / "scripts").glob("*.py")) if (root / "scripts").exists() else []
    with tempfile.TemporaryDirectory(prefix="software-certificate-skill-validate-") as temp_dir:
        for index, path in enumerate(scripts):
            try:
                py_compile.compile(
                    str(path), cfile=str(Path(temp_dir) / f"{index:03d}-{path.stem}.pyc"),
                    doraise=True
                )
            except (py_compile.PyCompileError, OSError) as exc:
                errors.append(f"Python compile failed {path.name}: {exc}")
    return {
        "status": "pass" if not errors else "fail",
        "root": str(root), "python_scripts": len(scripts), "json_files": len(json_files),
        "errors": errors, "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = validate(args.skill.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
