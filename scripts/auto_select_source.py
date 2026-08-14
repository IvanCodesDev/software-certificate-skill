#!/usr/bin/env python3
"""Rank and select attributable business source files without per-file user prompts."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

from common import (load_json, looks_like_backend, now_iso, relative_posix, safe_text,
                    save_json, sha256_file, under_backend_dir)

EXTENSIONS = {".py", ".cs", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".vue", ".go",
              ".rs", ".c", ".h", ".cpp", ".cc", ".php", ".rb", ".swift", ".m", ".mm",
              ".dart", ".sql", ".cshtml", ".razor", ".html"}
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
    # Map/argument forms such as put("client_secret","<32 hex>") and bare
    # `client_secret=<token>` in comments carry real credentials just as
    # often as a quoted assignment, and must not reach a public filing.
    "credential_literal": re.compile(
        r"(?i)\b(?:client[_-]?secret|app[_-]?secret|secret[_-]?key|api[_-]?key|"
        r"access[_-]?token|private[_-]?token)\b\W{0,4}[A-Za-z0-9_\-]{16,}"),
    "internal_url": re.compile(r"https?://(?:10\.|127\.0\.0\.1|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)"),
}


def classify_side(relative: str, text: str) -> str:
    """Attribute one file to the frontend or backend half of a fullstack repo."""
    if looks_like_backend(relative, text):
        return "backend"
    if under_backend_dir(relative):
        return "backend"
    if Path(relative).suffix.lower() == ".py":
        # Python next to a JS frontend runs on the server side even without
        # a framework import (services, models, jobs, utilities).
        return "backend"
    return "frontend"


def interleave_sides(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge both sides so any filing window shows frontend AND backend.

    Only the first 30 and last 30 pages of the corpus are filed once it
    exceeds 60 pages, so a score-sorted order can push the whole backend
    into the middle and out of the filing. Interleaving by accumulated line
    share keeps every contiguous window near the global side ratio while
    preserving score order inside each side.
    """
    front = [item for item in selected if item.get("side") != "backend"]
    back = [item for item in selected if item.get("side") == "backend"]
    if not front or not back:
        return selected
    total_front = sum(item["line_count"] for item in front) or 1
    total_back = sum(item["line_count"] for item in back) or 1
    merged: list[dict[str, Any]] = []
    front_index = back_index = front_lines = back_lines = 0
    while front_index < len(front) or back_index < len(back):
        take_front = back_index >= len(back) or (
            front_index < len(front) and front_lines * total_back <= back_lines * total_front)
        if take_front:
            merged.append(front[front_index])
            front_lines += front[front_index]["line_count"]
            front_index += 1
        else:
            merged.append(back[back_index])
            back_lines += back[back_index]["line_count"]
            back_index += 1
    return merged


BUILD_MARKERS = ("package.json", "pyproject.toml", "pom.xml", "go.mod", "pubspec.yaml")


def maven_aggregators(root: Path) -> dict[str, str]:
    """Artifact ids of in-repo POMs that only aggregate or manage modules."""
    found: dict[str, str] = {}
    for pom in root.rglob("pom.xml"):
        parts = {part.lower() for part in pom.relative_to(root).parts[:-1]}
        if parts & EXCLUDED_PARTS:
            continue
        text = safe_text(pom, 2_000_000) or ""
        if not re.search(r"<packaging>\s*pom\s*</packaging>", text):
            continue
        body = re.sub(r"(?s)<parent>.*?</parent>", "", text)
        artifact = re.search(r"<artifactId>([^<]+)</artifactId>", body)
        if artifact:
            found[artifact.group(1).strip()] = relative_posix(pom, root)
    return found


def maven_scope_key(pom: Path, aggregators: dict[str, str]) -> str | None:
    """Reactor identity of one Maven module; None for aggregator POMs.

    Modules inheriting an in-repo parent belong to one reactor and therefore
    to one application. An external parent such as spring-boot-starter-parent
    says nothing about ownership, so those fall back to their own groupId and
    two unrelated applications still register as separate scopes.
    """
    text = safe_text(pom, 2_000_000) or ""
    if re.search(r"<packaging>\s*pom\s*</packaging>", text):
        return None
    parent = re.search(r"(?s)<parent>(.*?)</parent>", text)
    if parent:
        artifact = re.search(r"<artifactId>([^<]+)</artifactId>", parent.group(1))
        group = re.search(r"<groupId>([^<]+)</groupId>", parent.group(1))
        if artifact and artifact.group(1).strip() in aggregators:
            return f"maven-reactor:{group.group(1).strip() if group else ''}:{artifact.group(1).strip()}"
    body = re.sub(r"(?s)<parent>.*?</parent>", "", text)
    group = re.search(r"<groupId>([^<]+)</groupId>", body)
    return f"maven:{group.group(1).strip()}" if group else f"maven:{pom.parent.name}"


def workspace_members(root: Path) -> set[str]:
    """Top-level directories a root manifest declares as workspace packages."""
    members: set[str] = set()
    patterns: list[str] = []
    package = root / "package.json"
    if package.is_file():
        try:
            declared = load_json(package).get("workspaces")
        except Exception:
            declared = None
        if isinstance(declared, dict):
            declared = declared.get("packages")
        if isinstance(declared, list):
            patterns += [str(item) for item in declared]
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        patterns += re.findall(r"(?m)^\s*-\s*['\"]?([^'\"\n]+)", safe_text(pnpm, 200_000) or "")
    for pattern in patterns:
        for path in root.glob(pattern.strip()):
            if path.is_dir():
                members.add(path.relative_to(root).parts[0])
    return members


def independent_scopes(root: Path) -> list[str]:
    """Top-level directories that look like separate applications.

    A multi-module build is one application: its modules are grouped by the
    reactor or workspace that declares them, so a microservice monorepo no
    longer asks the applicant to pick between twenty "scopes".
    """
    aggregators = maven_aggregators(root)
    workspace = workspace_members(root)
    groups: dict[str, list[str]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.lower() in EXCLUDED_PARTS:
            continue
        marker = next((name for name in BUILD_MARKERS if (path / name).exists()), None)
        if marker is None:
            continue
        if marker == "pom.xml":
            key = maven_scope_key(path / "pom.xml", aggregators)
            if key is None:
                continue
        elif path.name in workspace:
            key = "workspace:root"
        else:
            key = f"{marker}:{path.name}"
        groups.setdefault(key, []).append(path.name)
    return [names[0] if len(names) == 1 else f"{key}（{len(names)}个模块）"
            for key, names in sorted(groups.items())]


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


def scan_forbidden_markers(text: str, markers: list[str]) -> list[dict[str, Any]]:
    """Find project-configured third-party/legacy branding in source text."""
    findings: list[dict[str, Any]] = []
    folded = text.casefold()
    for marker in markers:
        value = str(marker).strip()
        if not value:
            continue
        start = folded.find(value.casefold())
        if start >= 0:
            findings.append({"marker": value, "line": text.count("\n", 0, start) + 1})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--analysis", type=Path,
                        help="project-analysis.json providing the architecture scope")
    parser.add_argument("--forbidden-marker", action="append", default=[],
                        help="literal source marker to exclude (repeatable)")
    parser.add_argument("--target-lines", type=int, default=4200)
    parser.add_argument("--max-files", type=int, default=240)
    args = parser.parse_args()
    root = args.project.resolve()
    scope = ""
    if args.analysis and args.analysis.is_file():
        analysis = load_json(args.analysis)
        scope = str(analysis.get("technology", {}).get("architecture_scope", {}).get("scope", ""))
    records: list[dict[str, Any]] = []
    duplicate_hashes: set[str] = set()
    secret_findings: list[dict[str, Any]] = []
    forbidden_marker_findings: list[dict[str, Any]] = []
    ownership_review: list[dict[str, Any]] = []
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
        marker_findings = scan_forbidden_markers(text, args.forbidden_marker)
        if marker_findings:
            forbidden_marker_findings.append({"path": rel, "findings": marker_findings})
            records.append({"path": rel, "decision": "exclude", "reason": "forbidden_source_marker",
                            "sha256": digest, "findings": marker_findings})
            continue
        if scope == "frontend_only" and looks_like_backend(rel, text):
            # Server-side code inside a frontend-only repository usually
            # belongs to a separate codebase: keep it out of the filing until
            # the applicant confirms ownership.
            ownership_review.append({"path": rel, "line_count": len(text.splitlines())})
            records.append({"path": rel, "decision": "exclude",
                            "reason": "backend_ownership_review", "sha256": digest})
            continue
        role, score, reasons = role_and_score(rel)
        records.append({
            "path": rel, "decision": "candidate", "role": role, "score": score,
            "line_count": len(text.splitlines()), "sha256": digest, "reasons": reasons,
            # Side attribution is always recorded so downstream balancing and
            # the release gate never depend on the analyzer's scope call.
            "side": classify_side(rel, text),
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
    # The filing must show the backend whenever the repository holds one.
    # scope=="fullstack" is the analyzer's call; the candidate-pool check is
    # the fail-safe for unclassified or missing analyses, so a weak scope
    # signal can never silently drop the backend from the material again.
    balance_trigger = ""
    if scope == "fullstack":
        balance_trigger = "architecture_scope_fullstack"
    elif scope not in {"frontend_only", "backend_only"}:
        candidate_front = sum(item["line_count"] for item in candidates if item["side"] != "backend")
        candidate_back = sum(item["line_count"] for item in candidates if item["side"] == "backend")
        if candidate_front >= 300 and candidate_back >= 300:
            balance_trigger = "both_sides_detected"
    side_balance: dict[str, Any] | None = None
    if balance_trigger:
        # Guarantee the backend a minimum share of the corpus, then
        # interleave both sides so the front-30 and back-30 filing windows
        # each contain backend source.
        backend_selected = sum(item["line_count"] for item in selected if item["side"] == "backend")
        backend_candidates = [item for item in candidates if item["side"] == "backend"]
        backend_floor = min(sum(item["line_count"] for item in backend_candidates),
                            int(args.target_lines * 0.35))
        for item in backend_candidates:
            if backend_selected >= backend_floor or len(selected) >= args.max_files:
                break
            if item["decision"] == "include":
                continue
            item["decision"] = "include"
            item["reason"] = "后端代表性配额"
            selected.append(item)
            selected_lines += item["line_count"]
            backend_selected += item["line_count"]
            represented_roles.add(item["role"])
        selected = interleave_sides(selected)
        side_balance = {
            "mode": "proportional_interleave", "trigger": balance_trigger,
            "backend_floor_lines": backend_floor,
            "frontend_files": sum(1 for item in selected if item["side"] != "backend"),
            "frontend_lines": sum(item["line_count"] for item in selected if item["side"] != "backend"),
            "backend_files": sum(1 for item in selected if item["side"] == "backend"),
            "backend_lines": backend_selected,
        }
    selected_paths = {item["path"] for item in selected}
    for item in records:
        if item.get("decision") == "candidate":
            item["decision"] = "exclude"
            item["reason"] = "业务相关性排序后未进入材料范围"
    scopes = independent_scopes(root)
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
            "architecture_scope": scope or "unclassified",
            "side_balance": side_balance,
            "unit": "whole_source_file", "mode": "automatic_evidence_ranked",
            "preferred_layers": ["entry", "route", "controller", "page", "service", "state", "domain", "algorithm"],
            "rationale": "综合项目结构、业务层次、真实源码行数、敏感信息和重复内容自动排序；"
                         "识别为全栈或前后端候选源码并存时，后端享有最低行数配额并与前端按行数占比混排，"
                         "保证前后交存卷都包含后端实现",
            "forbidden_source_markers": args.forbidden_marker,
        },
        "file_decisions": [
            {"path": item["path"], "role": item.get("role", "support"),
             "side": item.get("side", ""),
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
        "architecture_scope": scope or "unclassified",
        "side_distribution": side_balance,
        "ownership_review": ownership_review,
        "scope_confirmation_required": requires_confirmation,
        "secret_findings": secret_findings, "forbidden_marker_findings": forbidden_marker_findings,
        "decisions": records,
    }
    save_json(args.manifest.resolve(), manifest)
    save_json(args.report.resolve(), report)
    print(f"SOURCE_MANIFEST={args.manifest.resolve()}")
    print(f"SELECTED_FILES={len(selected)} LINES={selected_lines} SENSITIVE_EXCLUDED={len(secret_findings)} "
          f"ARCH_SCOPE={scope or 'unclassified'} OWNERSHIP_REVIEW={len(ownership_review)} "
          f"SCOPE_CONFIRMATION={str(requires_confirmation).lower()}"
          + (f" BACKEND_LINES={side_balance['backend_lines']}" if side_balance else ""))
    if not selected_paths:
        return 2
    return 3 if requires_confirmation else 0


if __name__ == "__main__":
    raise SystemExit(main())
