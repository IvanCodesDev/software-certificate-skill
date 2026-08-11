#!/usr/bin/env python3
"""Infer conservative application fields and technology facts from a real project."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from common import load_json, now_iso, relative_posix, safe_text, save_json, sha256_file

SOURCE_LANGUAGES = {
    ".py": "Python", ".java": "Java", ".kt": "Kotlin", ".go": "Go",
    ".php": "PHP", ".cs": "C#", ".c": "C", ".cpp": "C++", ".cc": "C++",
    ".h": "C/C++", ".m": "Objective-C", ".mm": "Objective-C++", ".swift": "Swift",
    ".dart": "Dart", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue", ".html": "HTML",
    ".rs": "Rust", ".rb": "Ruby", ".scala": "Scala", ".sql": "SQL",
}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", "bin", "obj",
             "coverage", "__pycache__", ".next", ".nuxt", ".venv", "venv",
             "软件著作权申请资料"}


def skipped(path: Path, root: Path) -> bool:
    return any(part.lower() in {value.lower() for value in SKIP_DIRS}
               for part in path.relative_to(root).parts[:-1])


def inference(value: Any, evidence: list[dict[str, Any]], confidence: str, reason: str,
              affects_registration: bool = False, needs_confirmation: bool = False) -> dict[str, Any]:
    return {
        "suggested_value": value, "evidence": evidence, "confidence": confidence,
        "reason": reason, "affects_registration": affects_registration,
        "needs_confirmation": needs_confirmation,
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def read_xml_value(path: Path, tags: list[str]) -> dict[str, str]:
    text = safe_text(path, 2_000_000) or ""
    result = {}
    for tag in tags:
        match = re.search(rf"<{tag}>([^<]+)</{tag}>", text, re.I)
        if match:
            result[tag] = match.group(1).strip()
    return result


def package_metadata(root: Path) -> tuple[list[dict[str, Any]], set[str], list[dict[str, str]]]:
    candidates: list[dict[str, Any]] = []
    dependencies: set[str] = set()
    commands: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or skipped(path, root):
            continue
        rel = relative_posix(path, root)
        name = path.name.lower()
        if name == "package.json":
            data = read_json(path)
            if data.get("name"):
                candidates.append({"field": "name", "value": data["name"], "source": rel})
            if data.get("version"):
                candidates.append({"field": "version", "value": data["version"], "source": rel})
            dependencies.update({str(item).lower() for group in (data.get("dependencies", {}), data.get("devDependencies", {})) for item in group})
            for key, value in data.get("scripts", {}).items():
                if key in {"dev", "start", "serve", "preview"}:
                    commands.append({"source": rel, "name": key, "command": f"npm run {key}", "script": str(value)})
        elif name == "pyproject.toml":
            text = safe_text(path) or ""
            for field in ("name", "version"):
                match = re.search(rf"(?m)^\s*{field}\s*=\s*[\"']([^\"']+)", text)
                if match:
                    candidates.append({"field": field, "value": match.group(1), "source": rel})
            dependencies.update(re.findall(r"(?i)\b(django|fastapi|flask|streamlit|pyqt\d?|pyside\d?)\b", text))
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            text = safe_text(path) or ""
            dependencies.update(re.findall(r"(?i)\b(spring-boot|spring-web|android|kotlin)\b", text))
            if name == "pom.xml":
                values = read_xml_value(path, ["artifactId", "version"])
                if values.get("artifactId"):
                    candidates.append({"field": "name", "value": values["artifactId"], "source": rel})
                if values.get("version"):
                    candidates.append({"field": "version", "value": values["version"], "source": rel})
        elif path.suffix.lower() in {".csproj", ".fsproj"}:
            values = read_xml_value(path, ["AssemblyName", "Version", "TargetFramework", "OutputType"])
            candidates.append({"field": "name", "value": values.get("AssemblyName", path.stem), "source": rel})
            if values.get("Version"):
                candidates.append({"field": "version", "value": values["Version"], "source": rel})
            dependencies.update(value.lower() for value in values.values())
        elif name == "pubspec.yaml":
            text = safe_text(path) or ""
            for field in ("name", "version"):
                match = re.search(rf"(?m)^{field}:\s*([^#\s]+)", text)
                if match:
                    candidates.append({"field": field, "value": match.group(1), "source": rel})
            if "flutter:" in text:
                dependencies.add("flutter")
        elif name in {"go.mod", "composer.json", "cargo.toml"}:
            text = safe_text(path) or ""
            dependencies.update(re.findall(r"(?i)\b(gin|fiber|echo|laravel|symfony|actix|rocket|tauri)\b", text))
    return candidates, dependencies, commands


def technology_profile(root: Path, dependencies: set[str]) -> dict[str, Any]:
    markers = {path.name.lower() for path in root.iterdir()}
    joined = " ".join(sorted(dependencies))
    frameworks: list[str] = []
    checks = {
        "Spring": r"spring", "Django": r"django", "FastAPI": r"fastapi", "Flask": r"flask",
        "React": r"react|next", "Vue": r"vue|nuxt", "Angular": r"angular",
        "Electron": r"electron", "Flutter": r"flutter", "Android": r"android",
        "ASP.NET": r"asp\.net|microsoft\.aspnetcore", "Laravel": r"laravel",
        "Tauri": r"tauri", "微信小程序": r"miniprogram|weixin|wechat",
    }
    for label, pattern in checks.items():
        if re.search(pattern, joined, re.I) or (label == "微信小程序" and "app.json" in markers):
            frameworks.append(label)
    project_types: list[str] = []
    if any(value in frameworks for value in ("React", "Vue", "Angular", "Spring", "Django", "FastAPI", "Flask", "ASP.NET", "Laravel")):
        project_types.append("web")
    if any(value in frameworks for value in ("Electron", "Tauri")) or any(root.rglob("*.sln")):
        project_types.append("desktop")
    if any(value in frameworks for value in ("Flutter", "Android", "微信小程序")) or any(root.rglob("*.xcodeproj")):
        project_types.append("mobile")
    if any(name in markers for name in ("docker-compose.yml", "docker-compose.yaml", "pnpm-workspace.yaml")):
        project_types.append("monorepo_or_services")
    if not project_types:
        project_types.append("cli_or_library")
    recommendation = "chrome_devtools" if "web" in project_types else ("computer_use" if {"desktop", "mobile"} & set(project_types) else "user_supplied")
    return {"frameworks": frameworks, "project_types": project_types, "screenshot_recommendation": recommendation}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.project.resolve()
    graph = load_json(args.evidence)
    metadata, dependencies, commands = package_metadata(root)
    languages: Counter[str] = Counter()
    source_lines = 0
    source_files = 0
    for path in root.rglob("*"):
        if not path.is_file() or skipped(path, root) or path.suffix.lower() not in SOURCE_LANGUAGES:
            continue
        text = safe_text(path, 20_000_000)
        count = len(text.splitlines()) if text is not None else 0
        languages[SOURCE_LANGUAGES[path.suffix.lower()]] += count
        source_lines += count
        source_files += 1
    tech = technology_profile(root, dependencies)
    names = [item for item in metadata if item["field"] == "name"]
    versions = [item for item in metadata if item["field"] == "version"]
    name_value = names[0]["value"] if names else root.name
    version_values = list(dict.fromkeys(str(item["value"]) for item in versions))
    version_value = version_values[0] if version_values else ""
    conflicts = []
    if len(version_values) > 1:
        conflicts.append({"field": "version", "values": version_values, "sources": versions,
                          "requires_confirmation": True})
    capabilities = []
    for node in graph.get("nodes", []):
        if node.get("type") != "capability_candidate":
            continue
        capabilities.append({
            "id": node["id"], "name": node.get("name", node["id"]),
            "evidence_ids": node.get("evidence_ids", []), "strength": node.get("strength", "weak"),
            "status": "candidate", "runtime_confirmation_required": True,
        })
    primary_languages = [name for name, _ in languages.most_common()]
    evidence_name = [{"path": item["source"], "value": item["value"]} for item in names[:5]]
    evidence_version = [{"path": item["source"], "value": item["value"]} for item in versions[:5]]
    result = {
        "schema_version": "1.0", "generated_at": now_iso(),
        "project": {"root": str(root), "name": root.name, "fingerprint": sha256_file(args.evidence)},
        "field_inferences": {
            "software_name_candidate": inference(name_value, evidence_name, "medium" if names else "low",
                                                   "来自正式项目配置；登记名称仍由申请人一次确认", True, True),
            "version_candidate": inference(version_value, evidence_version,
                                             "high" if len(version_values) == 1 else "low",
                                             "优先读取正式版本配置", True, len(version_values) != 1),
            "software_classification": inference(tech["project_types"], [{"frameworks": tech["frameworks"]}], "medium",
                                                   "由框架、构建配置和项目入口综合判断"),
            "programming_languages": inference(primary_languages, [{"line_counts": dict(languages)}], "high",
                                                 "按真实源码扩展名和行数统计"),
            "source_line_count": inference(source_lines, [{"source_files": source_files}], "high",
                                             "排除依赖和构建目录后统计可读源码"),
            "development_tools": inference(sorted(set(tech["frameworks"] + primary_languages)),
                                             [{"dependencies": sorted(dependencies)[:100]}], "medium",
                                             "由依赖和语言推断，采用保守表述"),
            "runtime_platform": inference(tech["project_types"], [{"commands": commands}], "medium",
                                            "由项目类型和运行命令推断"),
            "screenshot_mode": inference(tech["screenshot_recommendation"], [{"project_types": tech["project_types"]}],
                                          "high", "按真实项目交互形态推荐，最终由申请人选择", False, True),
        },
        "technology": {
            **tech, "languages_by_lines": dict(languages), "source_files": source_files,
            "source_lines": source_lines, "dependencies_detected": sorted(dependencies),
            "run_command_candidates": commands,
        },
        "capabilities": capabilities,
        "routes": [node for node in graph.get("nodes", []) if node.get("type") == "route"],
        "existing_screenshots": [node for node in graph.get("nodes", []) if node.get("type") == "screenshot"],
        "conflicts": conflicts,
        "business_understanding_status": "requires_model_synthesis_from_evidence_and_runtime",
        "warning": "功能候选只用于引导模型阅读代码和运行界面，不直接作为正式业务结论。",
    }
    save_json(args.output.resolve(), result)
    print(f"PROJECT_ANALYSIS={args.output.resolve()}")
    print(f"LANGUAGES={','.join(primary_languages)} SOURCE_LINES={source_lines} CAPABILITIES={len(capabilities)} CONFLICTS={len(conflicts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
