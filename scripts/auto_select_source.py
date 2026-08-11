#!/usr/bin/env python3
"""Rank and select attributable business source files without per-file user prompts."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

from common import now_iso, relative_posix, safe_text, save_json, sha256_file

EXTENSIONS = {".py", ".cs", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".vue", ".go",
              ".rs", ".c", ".h", ".cpp", ".cc", ".php", ".rb", ".swift", ".m", ".mm",
              ".dart", ".sql", ".cshtml", ".razor"}
EXCLUDED_PARTS = {".git", "node_modules", "vendor", "dist", "build", "bin", "obj", "target",
                  "coverage", "__pycache__", ".next", ".nuxt", "generated", "fixtures", "fixture",
                  "demo", "demos", "example", "examples", "samples", "软件著作权申请资料"}
EXCLUDED_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "composer.lock"}
ROLE_WEIGHTS = {
    "entry": 100, "route": 95, "router": 95, "controller": 90, "handler": 90,
    "page": 85, "view": 85, "screen": 85, "component": 75, "service": 88,
    "usecase": 88, "domain": 82, "model": 75, "entity": 75, "repository": 72,
    "store": 80, "state": 78, "api": 82, "client": 72, "algorithm": 90,
    "engine": 86, "core": 80, "util": 35, "helper": 30,
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"),
    "internal_url": re.compile(r"https?://(?:10\.|127\.0\.0\.1|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)"),
}


def role_and_score(relative: str) -> tuple[str, int, list[str]]:
    low = relative.lower().replace("\\", "/")
    stem = Path(low).stem
    score = 20
    reasons = []
    role = "support"
    for token, weight in ROLE_WEIGHTS.items():
        if token in stem or f"/{token}" in low:
            if weight > score:
                score = weight
                role = token
            reasons.append(f"path:{token}")
    if any(part in low for part in ("/src/", "/app/", "/lib/")):
        score += 12
        reasons.append("application_source")
    if any(part in low for part in ("test", "spec", "mock")):
        score -= 60
        reasons.append("test_or_mock")
    if Path(low).name in {"main.py", "app.py", "program.cs", "main.go", "index.ts", "index.js"}:
        score += 80
        role = "entry"
        reasons.append("application_entry")
    return role, score, sorted(set(reasons))


def skipped(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = {part.lower() for part in rel.parts[:-1]}
    return bool(parts & EXCLUDED_PARTS) or path.name.lower() in EXCLUDED_FILES or path.suffix.lower() not in EXTENSIONS


def scan_secrets(path: Path) -> list[dict[str, str]]:
    text = safe_text(path, 20_000_000)
    if text is None:
        return []
    findings = []
    for kind, pattern in SECRET_PATTERNS.items():
        for match in list(pattern.finditer(text))[:10]:
            line = text.count("\n", 0, match.start()) + 1
            findings.append({"type": kind, "line": str(line),
                             "fingerprint": hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()[:12]})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--target-lines", type=int, default=4200)
    parser.add_argument("--max-files", type=int, default=240)
    args = parser.parse_args()
    root = args.project.resolve()
    records: list[dict[str, Any]] = []
    duplicate_hashes: set[str] = set()
    secret_findings: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or skipped(path, root):
            continue
        text = safe_text(path, 20_000_000)
        if text is None:
            continue
        rel = relative_posix(path, root)
        digest = sha256_file(path)
        if digest in duplicate_hashes:
            records.append({"path": rel, "decision": "exclude", "reason": "duplicate_content", "sha256": digest})
            continue
        duplicate_hashes.add(digest)
        findings = scan_secrets(path)
        if findings:
            secret_findings.append({"path": rel, "findings": findings})
            records.append({"path": rel, "decision": "exclude", "reason": "sensitive_pattern", "sha256": digest})
            continue
        role, score, reasons = role_and_score(rel)
        records.append({
            "path": rel, "decision": "candidate", "role": role, "score": score,
            "line_count": len(text.splitlines()), "sha256": digest, "reasons": reasons,
        })
    candidates = sorted((item for item in records if item["decision"] == "candidate"),
                        key=lambda item: (-item["score"], item["path"].lower()))
    selected: list[dict[str, Any]] = []
    selected_lines = 0
    represented_roles: set[str] = set()
    for item in candidates:
        must_cover = item["role"] not in represented_roles and item["score"] >= 70
        if len(selected) >= args.max_files:
            break
        if selected_lines >= args.target_lines and not must_cover:
            continue
        if item["score"] < 20 and selected_lines >= min(1000, args.target_lines):
            continue
        item["decision"] = "include"
        item["reason"] = "、".join(item["reasons"] or ["真实可读源码"])
        selected.append(item)
        selected_lines += item["line_count"]
        represented_roles.add(item["role"])
    selected_paths = {item["path"] for item in selected}
    for item in records:
        if item.get("decision") == "candidate":
            item["decision"] = "exclude"
            item["reason"] = "业务相关性排序后未进入材料范围"
    scopes = []
    for path in root.iterdir():
        if path.is_dir() and any((path / marker).exists() for marker in ("package.json", "pyproject.toml", "pom.xml", "go.mod", "pubspec.yaml")):
            scopes.append(path.name)
    requires_confirmation = len(scopes) > 1
    manifest = {
        "include": ["**/*"],
        "exclude": [
            "**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**", "**/bin/**",
            "**/obj/**", "**/target/**", "**/coverage/**", "**/*.min.js", "**/*.generated.*",
            "**/*test*/**", "**/*fixture*/**", "**/*demo*/**", "**/*example*/**"
        ],
        "ordered_files": [item["path"] for item in selected],
        "selection_policy": {
            "scope": "申请软件自身且与主要功能直接相关的完整源文件",
            "unit": "whole_source_file", "mode": "automatic_evidence_ranked",
            "preferred_layers": ["entry", "route", "controller", "page", "service", "state", "domain", "algorithm"],
            "rationale": "综合项目结构、业务层次、真实源码行数、敏感信息和重复内容自动排序"
        },
        "file_decisions": [
            {"path": item["path"], "role": item.get("role", "support"),
             "evidence_ids": [], "reason": item.get("reason", item.get("reason", "")),
             "score": item.get("score"), "sha256": item.get("sha256")}
            for item in records
        ],
        "review": {
            "confirmed_by": "automatic evidence-ranked selector",
            "confirmed_at": now_iso(), "open_source_boundary_checked": True,
            "generated_code_boundary_checked": True, "secret_scan_checked": True,
            "requires_user_confirmation": requires_confirmation,
        }
    }
    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "project_root": str(root),
        "selected_files": len(selected), "selected_original_lines": selected_lines,
        "represented_roles": sorted(represented_roles), "scope_candidates": scopes,
        "scope_confirmation_required": requires_confirmation,
        "secret_findings": secret_findings, "decisions": records,
    }
    save_json(args.manifest.resolve(), manifest)
    save_json(args.report.resolve(), report)
    print(f"SOURCE_MANIFEST={args.manifest.resolve()}")
    print(f"SELECTED_FILES={len(selected)} LINES={selected_lines} SENSITIVE_EXCLUDED={len(secret_findings)} SCOPE_CONFIRMATION={str(requires_confirmation).lower()}")
    if not selected_paths:
        return 2
    return 3 if requires_confirmation else 0


if __name__ == "__main__":
    raise SystemExit(main())
