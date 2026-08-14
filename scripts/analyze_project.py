#!/usr/bin/env python3
"""Infer conservative application fields and technology facts from a real project."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from common import (load_json, looks_like_backend, now_iso, relative_posix, safe_text,
                    save_json, sha256_file, strongly_backend)

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
        elif (name.startswith("requirements") and name.endswith(".txt")) or name == "pipfile":
            # Python backends commonly declare frameworks only here; missing
            # this file used to misread fullstack repositories as pure frontend.
            text = safe_text(path) or ""
            for line in text.splitlines():
                match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)", line)
                if match:
                    dependencies.add(match.group(1).lower())
    return candidates, dependencies, commands


FRONTEND_DEPENDENCIES = re.compile(
    r"\b(vue|react|angular|svelte|vite|webpack|rollup|element-plus|ant-design|antd|"
    r"pinia|vuex|redux|next|nuxt|tailwind)\b", re.I)
BACKEND_DEPENDENCIES = re.compile(
    r"\b(express|koa|fastify|nestjs|eggjs|django|flask|fastapi|sanic|tornado|"
    r"spring-boot|spring-web|mybatis|gin|fiber|echo|laravel|symfony|actix|rocket|"
    r"uvicorn|gunicorn|"
    r"microsoft\.aspnetcore|sequelize|typeorm|prisma|mongoose|sqlalchemy)\b", re.I)


def find_marker(root: Path, name: str) -> str | None:
    for path in root.rglob(name):
        if path.is_file() and not skipped(path, root):
            return relative_posix(path, root)
    return None


def architecture_scope(root: Path, dependencies: set[str],
                       languages: Counter[str] | None = None,
                       backend_impl: dict[str, int] | None = None) -> dict[str, Any]:
    """Classify whether the repository holds frontend, backend, or both.

    The scope decides what the filing must contain: a fullstack repository
    files frontend AND backend source together, while a frontend-only
    repository must not silently ship vendored backend code someone else
    owns. Declared server frameworks, server markers and code volume decide
    the call; a stray `server/` folder or a few small foreign-language files
    stay weak evidence so vendored snippets are routed to ownership review
    instead of silently upgrading the project to "fullstack".

    Frontend code (components, pages, styles) routinely dwarfs the backend
    in line count, so the backend/total ratio alone would misread most real
    fullstack repositories as frontend-only and silently drop the backend
    from the filing. Strongly attributed implementation (framework imports
    or server-side directories) therefore counts by absolute volume and is
    never diluted by a large frontend.
    """
    languages = languages or Counter()
    impl = backend_impl or {"files": 0, "lines": 0, "attributed_lines": 0}
    frontend: list[str] = []
    backend: list[str] = []
    joined = " ".join(sorted(dependencies))
    if FRONTEND_DEPENDENCIES.search(joined):
        frontend.append("依赖含前端框架或构建工具")
    if (root / "index.html").is_file() or any(root.glob("vite.config.*")) or (root / "src" / "App.vue").is_file():
        frontend.append("存在前端入口与构建配置")
    env_text = " ".join(safe_text(path, 100_000) or "" for path in root.glob(".env*") if path.is_file())
    if re.search(r"(?i)(api[_-]?(url|base)|baseurl)\s*=", env_text):
        frontend.append("环境变量指向外部 API 地址")
    readme_path = root / "README.md"
    readme = (safe_text(readme_path, 500_000) or "") if readme_path.is_file() else ""
    if re.search(r"前端项目|前端仓库|\bfrontend\b", readme, re.I):
        frontend.append("README 声明为前端项目")

    strong_backend = False
    if BACKEND_DEPENDENCIES.search(joined):
        backend.append("依赖清单声明了服务端框架或数据访问层")
        strong_backend = True
    for marker in ("manage.py", "application.yml", "application.properties", "wsgi.py", "asgi.py"):
        found = find_marker(root, marker)
        if found:
            backend.append(f"存在服务端配置 {found}")
            strong_backend = True
            break
    for name in ("server", "backend", "api-server"):
        if (root / name).is_dir():
            backend.append(f"存在目录 {name}/（弱信号）")
    total_lines = sum(languages.values())
    backend_lines = impl["lines"]
    backend_ratio = backend_lines / total_lines if total_lines else 0.0
    attributed_lines = int(impl.get("attributed_lines", 0))
    if impl["lines"]:
        backend.append(f"源码级识别到服务端实现：{impl['files']}个文件/{impl['lines']}行"
                       f"（框架导入或服务端目录强归因 {attributed_lines} 行）")
    if backend_ratio >= 0.15:
        backend.append(f"服务端代码占比 {backend_ratio:.0%}")
    # A real backend is practically never under ~300 lines, so smaller
    # volumes without declared frameworks stay weak evidence (vendored
    # demos, stray legacy files) and go to ownership review instead.
    substantial_backend = backend_lines >= 300 and backend_ratio >= 0.15
    # Strong attribution reaching real-backend volume upgrades the scope on
    # its own: a big frontend must not dilute an actual in-repo backend.
    implemented_backend = attributed_lines >= 300
    if implemented_backend:
        backend.append(f"服务端强归因实现 {attributed_lines} 行，达到独立后端规模")

    if frontend and (strong_backend or substantial_backend or implemented_backend):
        scope = "fullstack"
    elif frontend:
        scope = "frontend_only"
    elif strong_backend or implemented_backend or backend_ratio >= 0.5:
        scope = "backend_only"
    else:
        scope = "unclassified"
    return {"scope": scope, "frontend_signals": frontend, "backend_signals": backend,
            "backend_language_ratio": round(backend_ratio, 4),
            "backend_implementation": impl}


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
    backend_impl = {"files": 0, "lines": 0, "attributed_lines": 0}
    for path in root.rglob("*"):
        if not path.is_file() or skipped(path, root) or path.suffix.lower() not in SOURCE_LANGUAGES:
            continue
        text = safe_text(path, 20_000_000)
        count = len(text.splitlines()) if text is not None else 0
        languages[SOURCE_LANGUAGES[path.suffix.lower()]] += count
        source_lines += count
        source_files += 1
        # Per-file backend attribution shared with the source selector, so
        # the analysis scope and the selection sides always agree. Framework
        # imports or a server-side directory count as strong attribution;
        # backend-only languages found elsewhere stay weak evidence.
        rel = relative_posix(path, root)
        strong = strongly_backend(rel, text or "")
        if strong or looks_like_backend(rel, text or ""):
            backend_impl["files"] += 1
            backend_impl["lines"] += count
            if strong:
                backend_impl["attributed_lines"] += count
    tech = technology_profile(root, dependencies)
    tech["architecture_scope"] = architecture_scope(root, dependencies, languages, backend_impl)
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
    print(f"LANGUAGES={','.join(primary_languages)} SOURCE_LINES={source_lines} CAPABILITIES={len(capabilities)} "
          f"CONFLICTS={len(conflicts)} SCOPE={tech['architecture_scope']['scope']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
