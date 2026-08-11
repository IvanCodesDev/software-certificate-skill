#!/usr/bin/env python3
"""Scan a real project into a conservative evidence graph."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

from common import (normalize_display_name, now_iso, relative_posix, safe_text,
                    save_json, sha256_file, stable_id)


SOURCE_EXTENSIONS = {
    ".py": "Python", ".cs": "C#", ".java": "Java", ".kt": "Kotlin",
    ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".vue": "Vue", ".go": "Go", ".rs": "Rust",
    ".c": "C", ".h": "C/C++", ".cpp": "C++", ".cc": "C++",
    ".php": "PHP", ".rb": "Ruby", ".swift": "Swift", ".sql": "SQL",
    ".cshtml": "Razor", ".razor": "Razor", ".html": "HTML"
}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".xml", ".config"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IGNORED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "vendor", "dist", "build",
    "bin", "obj", "target", ".idea", ".vscode", "coverage", "__pycache__",
    ".pytest_cache", ".next", ".nuxt", ".skill-staging"
}
SENSITIVE_NAMES = {
    ".env", "id_rsa", "id_dsa", "credentials.json", "secrets.json",
    "privatekey", "private_key", "keystore", "newtouchhisrsaprivatekey.txt"
}
GENERIC_STEMS = {
    "index", "main", "app", "program", "startup", "config", "configuration",
    "base", "common", "utils", "helper", "model", "entity", "service",
    "controller", "view", "component", "layout", "test", "tests"
}
ROUTE_PATTERNS = [
    re.compile(r"\[(?:HttpGet|HttpPost|HttpPut|HttpDelete|Route)\s*\(\s*[\"']([^\"']+)", re.I),
    re.compile(r"(?:path|route)\s*[:=]\s*[\"']([^\"']+)[\"']", re.I),
    re.compile(r"(?:router|app)\.(?:get|post|put|delete|patch)\s*\(\s*[\"']([^\"']+)", re.I),
    re.compile(r"@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*\(\s*[\"']([^\"']+)", re.I),
]
TITLE_PATTERNS = [
    re.compile(r"<(?:title|h1|h2)[^>]*>\s*([^<]{2,80})\s*</", re.I),
    re.compile(r"(?:title|label|name)\s*[:=]\s*[\"']([^\"']{2,80})[\"']", re.I),
]


def skipped(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part.lower() in IGNORED_DIRS for part in rel_parts[:-1]):
        return True
    low = path.name.lower()
    return low in SENSITIVE_NAMES or any(token in low for token in ("privatekey", "private_key", "secret.key"))


def image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-files", type=int, default=100000)
    parser.add_argument("--max-text-bytes", type=int, default=2_000_000)
    args = parser.parse_args()
    root = args.project.resolve()
    if not root.is_dir():
        parser.error(f"Project directory not found: {root}")

    nodes: list[dict] = []
    edges: list[dict] = []
    file_ids: dict[str, str] = {}
    capability_evidence: dict[str, set[str]] = defaultdict(set)
    capability_titles: dict[str, str] = {}
    counts: Counter[str] = Counter()

    files = [p for p in root.rglob("*") if p.is_file() and not skipped(p, root)]
    files.sort(key=lambda p: relative_posix(p, root).lower())
    if len(files) > args.max_files:
        parser.error(f"File count {len(files)} exceeds --max-files {args.max_files}")

    for path in files:
        rel = relative_posix(path, root)
        ext = path.suffix.lower()
        if ext not in set(SOURCE_EXTENSIONS) | CONFIG_EXTENSIONS | IMAGE_EXTENSIONS:
            continue
        digest = sha256_file(path)
        file_id = stable_id("FILE", rel)
        file_ids[rel] = file_id
        node: dict = {
            "id": file_id,
            "type": "source_file" if ext in SOURCE_EXTENSIONS else ("screenshot" if ext in IMAGE_EXTENSIONS else "config_file"),
            "path": rel,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "extension": ext
        }
        if ext in IMAGE_EXTENSIONS:
            width, height = image_size(path)
            node.update({"width": width, "height": height, "captured_at": None, "status": "candidate"})
            counts["screenshots"] += 1
        else:
            text = safe_text(path, args.max_text_bytes)
            node["language"] = SOURCE_EXTENSIONS.get(ext, "configuration")
            node["line_count"] = len(text.splitlines()) if text is not None else None
            counts["source_lines"] += node["line_count"] or 0
            if text is not None and ext in SOURCE_EXTENSIONS:
                routes: list[str] = []
                for pattern in ROUTE_PATTERNS:
                    routes.extend(pattern.findall(text))
                for route in sorted(set(routes))[:100]:
                    route_id = stable_id("ROUTE", route)
                    if not any(n["id"] == route_id for n in nodes):
                        nodes.append({"id": route_id, "type": "route", "path": route, "status": "candidate", "strength": "corroborating"})
                    edges.append({"from": file_id, "to": route_id, "type": "implements"})
                titles: list[str] = []
                for pattern in TITLE_PATTERNS:
                    titles.extend(pattern.findall(text))
                node["ui_title_candidates"] = sorted(set(t.strip() for t in titles if "{{" not in t))[:30]

        nodes.append(node)
        counts[node["type"]] += 1

        stem = path.stem.lower()
        if ext in SOURCE_EXTENSIONS and stem not in GENERIC_STEMS and len(stem) >= 3:
            cap_key = re.sub(r"(?:controller|service|component|page|view|form|handler|manager)$", "", stem, flags=re.I)
            cap_key = cap_key or stem
            cap_id = stable_id("CAP", cap_key)
            capability_evidence[cap_id].add(file_id)
            capability_titles.setdefault(cap_id, normalize_display_name(path.stem))

    for cap_id, evidence_ids in sorted(capability_evidence.items(), key=lambda item: (-len(item[1]), item[0])):
        nodes.append({
            "id": cap_id,
            "type": "capability_candidate",
            "name": capability_titles[cap_id],
            "status": "candidate",
            "strength": "weak" if len(evidence_ids) == 1 else "corroborating",
            "evidence_ids": sorted(evidence_ids),
            "requires_human_confirmation": True
        })
        for evidence_id in sorted(evidence_ids):
            edges.append({"from": evidence_id, "to": cap_id, "type": "suggests"})

    graph = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "project": {"root": str(root), "name": root.name},
        "nodes": nodes,
        "edges": edges,
        "summary": {
            **dict(counts),
            "files_indexed": len(file_ids),
            "capability_candidates": len(capability_evidence),
            "routes": sum(1 for n in nodes if n["type"] == "route"),
            "warning": "Capability candidates are investigation leads until confirmed against runtime behavior."
        }
    }
    save_json(args.output.resolve(), graph)
    print(f"EVIDENCE_GRAPH={args.output.resolve()}")
    print(f"FILES={len(file_ids)} CAPABILITIES={len(capability_evidence)} ROUTES={graph['summary']['routes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
