#!/usr/bin/env python3
"""Beginner-facing, resumable end-to-end software copyright material workflow."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common import load_json, now_iso, save_json, sha256_file, utf8_subprocess_env
from product_model import (ProductPaths, copy_changed, find_slots, hash_inputs, load_state,
                           prune_delivery_root, record_stage, safe_filename, snapshot_files,
                           stage_is_current, write_sha256s)


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
RULES_SNAPSHOT = "2026-08-11"


def progress(message: str) -> None:
    print(message, flush=True)


def run_script(name: str, arguments: list[str], allowed: set[int] | None = None,
               timeout_seconds: float | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT_DIR / name), *arguments]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=utf8_subprocess_env(),
        creationflags=creationflags, start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace", timeout=20)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate(timeout=20)
        raise RuntimeError(f"{name}超过工作流外层时限{timeout_seconds}秒；已清理子进程树：{stderr or stdout}")
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.returncode not in (allowed or {0}):
        raise RuntimeError(f"{name}执行失败({completed.returncode})：{completed.stderr or completed.stdout}")
    return completed


def validate_schema(instance_path: Path, schema_path: Path) -> list[str]:
    instance, schema = load_json(instance_path), load_json(schema_path)
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(str(v) for v in error.path)}: {error.message}" for error in validator.iter_errors(instance)]
    except ImportError:
        missing = [name for name in schema.get("required", []) if name not in instance]
        return [f"缺少字段：{name}" for name in missing]


def write_intake_card(paths: ProductPaths, analysis: dict[str, Any]) -> Path:
    card = paths.work / "一次性基础信息表.md"
    name = analysis.get("field_inferences", {}).get("software_name_candidate", {}).get("suggested_value", "")
    version = analysis.get("field_inferences", {}).get("version_candidate", {}).get("suggested_value", "")
    card.write_text(f"""# 一次性基础信息表

请集中填写登记事实。项目技术、业务功能、运行环境和源码范围由 Skill 自动分析，不在这里重复询问。

| 项目 | 填写内容 |
|---|---|
| 软件全称 | 建议：{name} |
| 软件简称（可选） |  |
| 版本号 | 建议：{version or 'V1.0'} |
| 著作权人类型、名称 |  |
| 证件类型、证件号码 |  |
| 开发完成日期 | YYYY-MM-DD |
| 开发方式 | 单独开发 / 合作开发 / 委托开发 / 下达任务开发 |
| 软件说明 | 原创 / 修改 |
| 发表状态 | 已发表 / 未发表（已发表需首次发表日期） |
| 权利取得方式 | 原始取得 / 继受取得 |
| 权利范围 | 全部权利 / 部分权利 |
| 补充权属说明 | 无则留空 |

机器读取文件：`一次性基础信息表.json`。只需填写一次；已有会话事实应由 Agent 自动写入并复用。
""", encoding="utf-8")
    return card


def write_screenshot_card(paths: ProductPaths, analysis: dict[str, Any]) -> tuple[Path, Path]:
    recommended = analysis.get("technology", {}).get("screenshot_recommendation", "user_supplied")
    labels = {"chrome_devtools": "Chrome DevTools", "computer_use": "Computer Use",
              "user_supplied": "用户自行截图", "skip": "暂时跳过截图"}
    card = paths.work / "截图方式选择卡.md"
    card.write_text(f"""# 截图方式选择卡

推荐：**{labels.get(recommended, recommended)}**（依据当前项目交互形态）。最终选择记录在一次性基础信息表中。

正式资料模式默认要求截图全部完成；只有明确把 `screenshot_policy` 设为 `draft_allowed` 时，才允许暂时跳过并生成带占位图的草稿。

1. **Chrome DevTools**：浏览器 Web 系统；自动启动/连接、断言页面、操作并保存截图。
2. **Computer Use**：桌面端、Electron、模拟器或复杂交互；按当前应用状态操作并取证。
3. **用户自行截图**：验证码、真机或敏感数据场景；Agent 将用户选定图片导入临时运行区后自动检查和匹配。
4. **暂时跳过截图**：仅限草稿模式；正式资料不会继续生成。
""", encoding="utf-8")
    task = paths.work / "截图任务清单.md"
    capabilities = analysis.get("capabilities", [])
    lines = ["# 截图任务清单", "", "每张图保留完整窗口、页面标题和操作结果；避免真实身份证号、手机号和密钥。", ""]
    if capabilities:
        for index, item in enumerate(capabilities, 1):
            lines.append(f"- `{index:02d}-{safe_filename(item.get('name', '功能'))}.png`：打开该功能并完成一次可见操作结果。")
    else:
        lines += ["- `01-启动或首页.png`：软件成功启动后的主界面。", "- `02-核心功能.png`：完成一次核心操作后的结果界面。"]
    task.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return card, task


def screenshot_policy(facts: dict[str, Any]) -> str:
    """Return the capture policy, defaulting safely to formal-required."""
    return facts.get("screenshot_policy", "required") if facts.get("screenshot_policy") in {
        "required", "draft_allowed"
    } else "required"


def screenshot_plan_fingerprint(paths: ProductPaths, facts: dict[str, Any],
                                business_path: Path) -> str:
    """Invalidate plans when business, source, URL, mode or server changes."""
    build_artifacts: list[tuple[str, int, str]] = []
    for pattern in ("target/*.jar", "target/*.war", "build/libs/*.jar", "build/*.exe", "dist/*.exe"):
        for artifact in sorted(paths.project.glob(pattern)):
            if artifact.is_file():
                build_artifacts.append((artifact.relative_to(paths.project).as_posix(),
                                        artifact.stat().st_size, sha256_file(artifact)))
    extra = {
        "mode": facts.get("screenshot_mode"),
        "policy": screenshot_policy(facts),
        "base_url": facts.get("screenshot_base_url", ""),
        "server": facts.get("screenshot_server", {}),
        "build_fingerprint": facts.get("screenshot_build_fingerprint", ""),
        "build_artifacts": build_artifacts,
    }
    return hash_inputs([business_path, paths.project], extra)


def archive_stale_screenshot_state(paths: ProductPaths) -> Path | None:
    """Move stale evidence aside instead of silently reusing it or deleting it."""
    items = [paths.work / "screenshot-plan.json", paths.work / "screenshot-index.json",
             paths.work / "screenshots"]
    existing = [item for item in items if item.exists()]
    if not existing:
        return None
    archive = paths.work / "stale-screenshot-runs" / now_iso().replace(":", "").replace("+", "_")
    archive.mkdir(parents=True, exist_ok=True)
    for item in existing:
        shutil.move(str(item), str(archive / item.name))
    return archive


def build_screenshot_plan(facts: dict[str, Any], business: dict[str, Any],
                          fingerprint: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    captures = []
    for position, capability in enumerate(business.get("capabilities", []), 1):
        shot_ids = capability.get("screenshot_ids") or [f"capability-{position:03d}"]
        for shot in shot_ids:
            captures.append({
                "id": shot, "title": capability.get("name", shot),
                "role": capability.get("actor", business.get("target_users")),
                "evidence_ids": capability.get("evidence_ids", []),
                "chapter": capability.get("name", ""),
                "route": capability.get("route") or (capability.get("entry") if str(capability.get("entry", "")).startswith("/") else None),
            })
    base_url = facts.get("screenshot_base_url", "")
    server = dict(facts.get("screenshot_server") or {})
    health_url = server.get("health_url")
    if not base_url and health_url:
        parsed = urlsplit(str(health_url))
        if parsed.scheme and parsed.netloc:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
    plan = {
        "schema_version": "1.0", "managed_by": "product_workflow",
        "input_sha256": fingerprint, "mode": facts.get("screenshot_mode"),
        "capture_policy": screenshot_policy(facts), "base_url": base_url,
        "captures": captures,
    }
    if server:
        plan["server"] = {key: value for key, value in server.items() if value not in (None, "")}
    forbidden_markers = [str(item) for item in facts.get("screenshot_forbidden_markers", []) if str(item).strip()]
    if forbidden_markers:
        plan["forbidden_markers"] = sorted(set(forbidden_markers))
    # Preserve a user's browser/setup/quality configuration across a business refresh.
    if previous:
        for key in ("default_timeout_ms", "browser", "setup", "quality"):
            if key in previous:
                plan[key] = previous[key]
        if "screenshot_server" not in facts and previous.get("server"):
            plan["server"] = previous["server"]
        if not plan.get("base_url") and previous.get("base_url"):
            plan["base_url"] = previous["base_url"]
    return plan


def stamp_screenshot_index(path: Path, policy: str, fingerprint: str) -> dict[str, Any]:
    data = load_json(path)
    data["capture_policy"] = policy
    data["plan_input_sha256"] = fingerprint
    save_json(path, data)
    return data


def prepare(paths: ProductPaths) -> int:
    state = load_state(paths)
    progress("正在检查运行环境")
    environment = paths.work / "environment.json"
    save_json(environment, {"generated_at": now_iso(), "python": sys.version, "platform": sys.platform,
                            "rules_snapshot": RULES_SNAPSHOT, "skill_root": str(SKILL_ROOT)})
    record_stage(paths, state, "environment", "complete", hash_inputs([SCRIPT_DIR]), [environment],
                 f"规则快照：{RULES_SNAPSHOT}")
    progress("正在分析项目")
    evidence, analysis = paths.work / "evidence-graph.json", paths.work / "project-analysis.json"
    scan_hash = hash_inputs([paths.project], {"scanner": sha256_file(SCRIPT_DIR / "scan_project.py"),
                                              "analyzer": sha256_file(SCRIPT_DIR / "analyze_project.py")})
    if not stage_is_current(state, "project_analysis", scan_hash):
        run_script("scan_project.py", ["--project", str(paths.project), "--output", str(evidence)])
        run_script("analyze_project.py", ["--project", str(paths.project), "--evidence", str(evidence), "--output", str(analysis)])
        record_stage(paths, state, "project_analysis", "complete", scan_hash, [evidence, analysis])
    analysis_data = load_json(analysis)
    intake = paths.work / "一次性基础信息表.json"
    if not intake.exists():
        example = load_json(SKILL_ROOT / "assets/examples/intake.example.json")
        suggested_name = analysis_data.get("field_inferences", {}).get("software_name_candidate", {}).get("suggested_value")
        suggested_version = analysis_data.get("field_inferences", {}).get("version_candidate", {}).get("suggested_value")
        if suggested_name:
            example["software_full_name"] = f"【待确认：{suggested_name}】"
        if suggested_version:
            example["version"] = str(suggested_version).upper().removeprefix("V")
            example["version"] = "V" + example["version"]
        save_json(intake, example)
    intake_card = write_intake_card(paths, analysis_data)
    business = paths.work / "business-understanding.json"
    if not business.exists():
        shutil.copy2(SKILL_ROOT / "assets/examples/business-understanding.example.json", business)
    synthesis = paths.work / "业务理解任务.md"
    synthesis.write_text("""# 模型业务理解任务

Agent 读取 evidence-graph.json、project-analysis.json、README、路由、页面、服务、模型、测试、部署配置和真实运行结果，完成 business-understanding.json。不得只凭文件名下结论；每项 capability 必须带 evidence_ids，界面事实优先由运行结果确认。purpose 必须写清业务对象、动作和结果，禁止重复“用于支撑实际业务处理”等套话；按真实页面差异填写 inputs、outputs、business_rules、state_changes、result_fields 和 error_cases。可用 manual_titles 为复杂功能提供贴合业务的过程、结果和异常小节标题。该文件是内部工作件，不转交普通用户填写。
""", encoding="utf-8")
    screenshot_card, screenshot_task = write_screenshot_card(paths, analysis_data)
    record_stage(paths, state, "business_understanding", "needs_model_synthesis", hash_inputs([analysis, evidence]), [business, synthesis])
    record_stage(paths, state, "intake", "needs_confirmation", hash_inputs([intake]), [intake, intake_card, screenshot_card, screenshot_task])
    print(f"DELIVERY_ROOT={paths.root}")
    print(f"RUNTIME_ROOT={paths.runtime}")
    print(f"INTAKE={intake}")
    print(f"BUSINESS_MODEL={business}")
    return 3


def screenshot_index(paths: ProductPaths, facts: dict[str, Any], business_path: Path) -> tuple[Path, str]:
    mode = facts.get("screenshot_mode")
    index = paths.work / "screenshot-index.json"
    plan = paths.work / "screenshot-plan.json"
    business = load_json(business_path)
    policy = screenshot_policy(facts)
    fingerprint = screenshot_plan_fingerprint(paths, facts, business_path)
    previous: dict[str, Any] | None = None
    rebuild = not plan.exists()
    if plan.exists():
        try:
            previous = load_json(plan)
        except Exception:
            previous = None
        old_fingerprint = previous.get("input_sha256") if previous else None
        # Plans from older releases had no fingerprint. Treat them as stale
        # once, while preserving their browser/setup/server settings below.
        if not previous or old_fingerprint != fingerprint:
            archive_stale_screenshot_state(paths)
            rebuild = True
    if rebuild:
        save_json(plan, build_screenshot_plan(facts, business, fingerprint, previous))
    plan_data = load_json(plan)
    if mode == "user_supplied":
        run_script("ingest_user_screenshots.py", ["--source", str(paths.screenshots), "--output", str(paths.work / "screenshots"),
                   "--plan", str(plan), "--report", str(index)], {0, 3})
        data = stamp_screenshot_index(index, policy, fingerprint)
        return index, data.get("state", "failed")
    elif mode == "skip":
        if policy == "required":
            save_json(index, {"schema_version": "1.0", "generated_at": now_iso(), "mode": mode,
                              "state": "awaiting_capture", "capture_policy": policy,
                              "blocking_reason": "正式资料模式禁止跳过截图；请完成截图后继续",
                              "captures": [], "plan": str(plan), "plan_input_sha256": fingerprint,
                              "summary": {"requested": len(plan_data.get("captures", [])), "passed": 0,
                                          "errors": 1, "quality_warnings": 0, "missing_planned": len(plan_data.get("captures", []))}})
            return index, "awaiting_capture"
        save_json(index, {"schema_version": "1.0", "generated_at": now_iso(), "mode": "skip",
                          "state": "skipped_by_user", "capture_policy": policy, "draft_allowed": True, "captures": [],
                          "plan_input_sha256": fingerprint,
                          "summary": {"requested": len(plan_data.get("captures", [])), "passed": 0,
                                      "errors": 0, "quality_warnings": 0, "missing_planned": 0}})
        return index, "skipped_by_user"
    elif mode == "chrome_devtools":
        plan_data = load_json(plan)
        runnable = bool(plan_data.get("base_url") and plan_data.get("captures") and
                        all(item.get("url") or item.get("route") for item in plan_data.get("captures", [])))
        if runnable:
            capture_dir = paths.work / "screenshots"
            run_script("capture_web_screenshots.py", ["--plan", str(plan), "--output", str(capture_dir),
                       "--evidence-source", str(paths.work / "evidence-graph.json"),
                       "--evidence-output", str(paths.work / "evidence-graph.with-screenshots.json")], {0, 2, 3}, 600)
            shutil.copy2(capture_dir / "screenshot-index.json", index)
            data = stamp_screenshot_index(index, policy, fingerprint)
            return index, data.get("state", "failed")
    elif mode == "computer_use":
        if index.exists() and load_json(index).get("state") == "captured":
            return index, "captured"
        session = paths.root / "computer-use-session.json"
        if session.exists():
            run_script("finalize_agent_screenshots.py", ["--plan", str(plan), "--session", str(session),
                       "--output", str(paths.work / "screenshots"), "--report", str(index),
                       "--evidence-source", str(paths.work / "evidence-graph.json"),
                       "--evidence-output", str(paths.work / "evidence-graph.with-screenshots.json")], {0, 2})
            data = stamp_screenshot_index(index, policy, fingerprint)
            return index, data.get("state", "failed")
    plan_count = len(load_json(plan).get("captures", []))
    save_json(index, {"schema_version": "1.0", "generated_at": now_iso(), "mode": mode,
                      "capture_policy": policy, "state": "awaiting_capture", "captures": [], "plan": str(plan),
                      "plan_input_sha256": fingerprint,
                      "summary": {"requested": plan_count, "passed": 0, "errors": 1,
                                  "quality_warnings": 0, "missing_planned": plan_count}})
    return index, "awaiting_capture"


def provenance_density(provenance: dict[str, Any]) -> tuple[bool, str]:
    """Check effective source lines before spending time rendering documents."""
    pages = provenance.get("pages", [])
    quota = int(provenance.get("lines_per_page", 50))
    effective = [int(item.get("effective_lines", item.get("line_count", 0))) for item in pages]
    short = [index + 1 for index, value in enumerate(effective[:-1]) if value < quota]
    if short:
        return False, f"非末页有效源码行不足{quota}行：{short[:12]}"
    return bool(effective), f"非末页均达到{quota}行；末页{effective[-1] if effective else 0}行（末页例外）"


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# 材料一致性校验报告", "", f"- 生成时间：{report.get('generated_at')}",
             f"- 结论：{'通过' if report.get('release_ready') else '仍有阻塞项'}", "", "## 检查结果", ""]
    for item in report.get("checks", []):
        lines.append(f"- {'通过' if item['status'] == 'pass' else '需处理'}｜{item['name']}：{item['detail']}")
    return "\n".join(lines) + "\n"


def generate(paths: ProductPaths, intake_path: Path | None, business_path: Path | None) -> int:
    state = load_state(paths)
    analysis = paths.work / "project-analysis.json"
    evidence = paths.work / "evidence-graph.json"
    # Re-analyse when the project OR the scanner/analyzer scripts changed:
    # a stale analysis would silently drop newly added inference fields.
    scan_hash = hash_inputs([paths.project], {"scanner": sha256_file(SCRIPT_DIR / "scan_project.py"),
                                              "analyzer": sha256_file(SCRIPT_DIR / "analyze_project.py")})
    if not analysis.exists() or not stage_is_current(state, "project_analysis", scan_hash):
        prepare(paths)
        state = load_state(paths)
    intake = (intake_path or paths.work / "一次性基础信息表.json").resolve()
    business = (business_path or paths.work / "business-understanding.json").resolve()
    errors = validate_schema(intake, SKILL_ROOT / "assets/schemas/intake.schema.json")
    errors += validate_schema(business, SKILL_ROOT / "assets/schemas/business-understanding.schema.json")
    slots = find_slots({"intake": load_json(intake), "business": load_json(business)})
    if errors or slots:
        pending = paths.quality / "待确认事项清单.md"
        pending.write_text("# 待确认事项清单\n\n" + "\n".join(f"- {item}" for item in errors + slots) + "\n", encoding="utf-8")
        print(f"PENDING={pending}")
        return 3
    facts = load_json(intake)
    save_json(paths.work / "application-facts.json", facts)
    record_stage(paths, state, "intake", "complete", hash_inputs([intake]), [intake, paths.work / "application-facts.json"])
    record_stage(paths, state, "business_understanding", "complete", hash_inputs([business, analysis, evidence]), [business])

    progress("正在选择真实源码")
    manifest, selection_report = paths.work / "source-manifest.json", paths.work / "source-selection-report.json"
    source_args = ["--project", str(paths.project), "--manifest", str(manifest),
                   "--report", str(selection_report), "--analysis", str(analysis)]
    for marker in facts.get("source_forbidden_markers", []):
        if str(marker).strip():
            source_args += ["--forbidden-marker", str(marker)]
    selected = run_script("auto_select_source.py", source_args, {0, 3})
    if selected.returncode == 3:
        pending = paths.quality / "待确认事项清单.md"
        scopes = load_json(selection_report).get("scope_candidates", [])
        pending.write_text("# 待确认事项清单\n\n- 项目包含多个独立应用范围，请一次确认本次申请范围：" + "、".join(scopes) + "\n", encoding="utf-8")
        return 3
    record_stage(paths, state, "source_selection", "complete", hash_inputs([manifest, business]), [manifest, selection_report])

    progress("正在获取页面截图")
    screenshots, screenshot_state = screenshot_index(paths, facts, business)
    placeholders = screenshot_state != "captured"
    record_stage(paths, state, "screenshots", "complete" if not placeholders else screenshot_state,
                 hash_inputs([screenshots, business]), [screenshots])
    if placeholders and screenshot_policy(facts) == "required":
        pending = paths.quality / "待确认事项清单.md"
        pending.write_text(
            "# 待确认事项清单\n\n"
            "- 正式资料模式要求先完成全部截图，当前状态为："
            f"{screenshot_state}。请检查截图计划、服务地址、启动命令、健康检查和登录步骤后重新运行。\n",
            encoding="utf-8")
        record_stage(paths, state, "release", "blocked_by_screenshots",
                     hash_inputs([screenshots, business]), [screenshots, pending],
                     "正式资料模式禁止在截图缺失时继续生成手册和代码材料")
        progress("正式资料需要先完成截图，当前流程已暂停")
        print(f"SCREENSHOT_REQUIRED={pending}")
        return 2

    progress("正在生成操作手册")
    manual_json, manual_docx = paths.work / "manual-content.json", paths.work / "manual.docx"
    manual_args = ["--facts", str(paths.work / "application-facts.json"), "--business", str(business),
                   "--screenshots", str(screenshots), "--output", str(manual_json)]
    if placeholders:
        manual_args.append("--allow-placeholders")
    run_script("generate_manual_content.py", manual_args, {0, 4})
    run_script("build_manual.py", ["--input", str(manual_json), "--theme", str(SKILL_ROOT / "assets/themes/standard-filing-gray.json"),
               "--output", str(manual_docx)])
    record_stage(paths, state, "manual", "complete", hash_inputs([manual_json, screenshots]), [manual_json, manual_docx])

    progress("正在生成代码材料")
    code_dir = paths.work / "code-material"
    code_dir.mkdir(parents=True, exist_ok=True)
    # Width/font shrink per retry while the exact leading keeps 50 lines
    # filling the A4 page body (~707.5pt), so no volume ships with a large
    # blank band at the bottom of every page.
    # Wider columns mean fewer wrapped rows, which keeps 50 effective source
    # lines inside one page grid. Retries narrow the text and shrink the font.
    configurations = [(87, 9.0, None), (83, 8.5, None), (78, 8.0, None)]
    provenance = code_dir / "source-provenance.json"
    render_dir = paths.work / "render-reports"
    render_dir.mkdir(parents=True, exist_ok=True)
    software = safe_filename(facts["software_full_name"])
    # Code volume PDFs are drawn directly on a fixed line grid (pagination is
    # correct by construction and takes milliseconds). Office conversion stays
    # as the fallback when reportlab or a TrueType CJK font is unavailable.
    code_pdf_engine = os.environ.get("SOFTCERT_CODE_PDF_ENGINE", "direct")
    formal_specs: list[tuple[Path, str, int, str]] = []
    converted: list[tuple[Path, Path, Path]] = []
    last_error = None
    for width, font, spacing in configurations:
        compose_args = ["--project", str(paths.project), "--manifest", str(manifest),
                        "--facts", str(paths.work / "application-facts.json"),
                        "--output-dir", str(code_dir),
                        "--max-chars", str(width), "--font-size", str(font)]
        if spacing is not None:
            compose_args += ["--line-spacing", str(spacing)]
        run_script("compose_code.py", compose_args)
        prov = load_json(provenance)
        density_ok, density_detail = provenance_density(prov)
        if not density_ok:
            last_error = RuntimeError(f"代码分页密度检查失败：{density_detail}")
            progress("代码页有效行不足，正在尝试下一种排版配置")
            continue
        formal_specs = []
        if "all" in prov["filing_groups"]:
            formal_specs.append((code_dir / "source-all.docx", f"{software}-代码(全部)",
                                 prov["filing_groups"]["all"]["page_count"], "all"))
        else:
            formal_specs += [(code_dir / "source-front-30.docx", f"{software}-代码(前30页)", 30, "front_30"),
                             (code_dir / "source-back-30.docx", f"{software}-代码(后30页)", 30, "back_30")]
        converted = []
        try:
            for docx, name, expected_pages, volume in formal_specs:
                pdf, report = code_dir / f"{name}.pdf", render_dir / f"{name}.json"
                if code_pdf_engine == "direct":
                    try:
                        run_script("render_code_pdf.py", ["--provenance", str(provenance),
                                   "--facts", str(paths.work / "application-facts.json"),
                                   "--volume", volume, "--pdf", str(pdf), "--report", str(report),
                                   "--render-dir", str(paths.work / "rendered" / name),
                                   "--expected-pages", str(expected_pages)], timeout_seconds=120)
                    except RuntimeError:
                        code_pdf_engine = "legacy"
                if code_pdf_engine == "legacy":
                    run_script("convert_document.py", ["--input", str(docx), "--pdf", str(pdf), "--report", str(report),
                               "--render-dir", str(paths.work / "rendered" / name), "--expected-pages", str(expected_pages)],
                               timeout_seconds=420)
                converted.append((docx, pdf, report))
            last_error = None
            break
        except RuntimeError as exc:
            last_error = exc
            progress("正在自动修复问题")
    if last_error:
        raise last_error
    record_stage(paths, state, "code_material", "complete", hash_inputs([manifest, provenance]), [provenance] + [item for group in converted for item in group[:2]])

    progress("正在整理申请表")
    application_txt = paths.work / "申请表信息.txt"
    application_model = paths.work / "application-form-model.json"
    run_script("generate_application_form.py", ["--facts", str(paths.work / "application-facts.json"), "--analysis", str(analysis),
               "--business", str(business), "--provenance", str(provenance), "--output", str(application_txt),
               "--model-output", str(application_model)])
    record_stage(paths, state, "application_form", "complete", hash_inputs([intake, business, provenance]), [application_txt, application_model])

    progress("正在渲染并检查文档")
    manual_pdf, manual_render = paths.work / "manual.pdf", render_dir / "操作手册.json"
    run_script("convert_document.py", ["--input", str(manual_docx), "--pdf", str(manual_pdf), "--report", str(manual_render),
               "--render-dir", str(paths.work / "rendered" / "操作手册")], timeout_seconds=420)
    record_stage(paths, state, "render", "complete", hash_inputs([manual_docx] + [item[0] for item in converted]),
                 [manual_pdf, manual_render] + [item[1] for item in converted])

    publication_root = paths.draft
    existing_draft = [path for path in publication_root.iterdir() if path.is_file()]
    snapshot = snapshot_files(paths, existing_draft, "生成新的材料草稿")
    for path in existing_draft:
        path.unlink()
    publication: list[tuple[Path, Path]] = [
        (application_txt, publication_root / "申请表信息.txt"),
        (manual_docx, publication_root / f"{software}_操作手册.docx"),
        (manual_pdf, publication_root / f"{software}_操作手册.pdf"),
    ]
    for (docx, pdf, _), (_, name, _, _) in zip(converted, formal_specs):
        publication += [(docx, publication_root / f"{name}.docx"), (pdf, publication_root / f"{name}.pdf")]
    for source, destination in publication:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    shutil.copy2(provenance, paths.quality / "代码来源追溯清单.json")
    shutil.copy2(screenshots, paths.quality / "截图清单.json")
    pending_items = []
    if placeholders:
        pending_items.append("截图尚未完整获取：当前操作手册含可见截图预留位置；补图后仅重建手册和相关报告。")
    selection_data = load_json(selection_report)
    if selection_data.get("architecture_scope") == "frontend_only":
        pending_items.append(
            "项目识别为纯前端仓库：交存源码仅含前端实现，后端为独立系统。"
            "请确认软件名称与申请范围一致；申请表运行支撑环境已按“需配合后端接口服务”口径核验。")
    elif selection_data.get("architecture_scope") == "fullstack":
        sides = selection_data.get("side_distribution") or {}
        pending_items.append(
            f"项目识别为全栈仓库：源码材料已同时纳入前端与后端实现"
            f"（后端 {sides.get('backend_files', 0)} 个文件 / {sides.get('backend_lines', 0)} 行，"
            "并按行数占比混排以保证前后交存卷都包含后端代码）。"
            "请确认全部后端代码归属于本申请软件；若后端另行单独登记，请改用纯前端口径重新生成。")
    elif selection_data.get("side_distribution"):
        sides = selection_data["side_distribution"]
        pending_items.append(
            f"架构范围未能明确判定，但仓库同时存在前端与后端实质源码，材料已按行数占比混排"
            f"（后端 {sides.get('backend_files', 0)} 个文件 / {sides.get('backend_lines', 0)} 行）。"
            "请确认前后端代码均归属于本申请软件。")
    ownership = selection_data.get("ownership_review", [])
    if ownership:
        listed = "、".join(item["path"] for item in ownership[:8])
        pending_items.append(
            f"以下 {len(ownership)} 个文件疑似后端/异构实现，已自动排除出代码材料，请确认归属："
            f"{listed}{'等' if len(ownership) > 8 else ''}。确属本申请软件时在源码清单中恢复后重新生成。")
    (paths.quality / "待确认事项清单.md").write_text("# 待确认事项清单\n\n" +
        ("\n".join(f"- {item}" for item in pending_items) if pending_items else "无必须人工确认的问题。") + "\n", encoding="utf-8")
    (paths.quality / "生成报告.md").write_text(f"""# 生成报告

- 软件：{facts['software_full_name']} {facts['version']}
- 生成时间：{now_iso()}
- 规则快照：{RULES_SNAPSHOT}
- 源程序逻辑页：{load_json(provenance).get('full_page_count')}
- 截图方式：{facts.get('screenshot_mode')}
- 当前材料目录：{publication_root}
- 截图状态：{screenshot_state}
- 上一版本备份：{snapshot or '无（首次生成）'}

只有一致性校验报告中的 `release_ready=true` 时，文件才会复制到“正式资料”；否则仅保留为草稿。
""", encoding="utf-8")
    code_upload = "全部代码PDF" if "all" in load_json(provenance)["filing_groups"] else "代码前30页PDF与后30页PDF"
    (paths.quality / "提交材料清单.md").write_text(f"""# 提交材料清单

当前状态：{'等待截图完成，仅供草稿复核' if placeholders else '等待最终一致性校验'}。

1. 仅在材料进入“正式资料”目录后，打开 `申请表信息.txt` 并复制到登记系统对应字段。
2. 核对软件全称、版本、著作权人、日期、发表状态和权利范围。
3. 按登记系统当日页面要求上传 `{software}_操作手册.pdf`。
4. 上传{code_upload}；DOCX保留作复核和修改。
5. 提交前确认工作流不存在登记事实或权属阻断项。

内部留档：生成报告、校验报告、代码来源、截图清单和 SHA-256 哈希。
""", encoding="utf-8")

    progress("正在自动修复问题")
    verification = paths.quality / "材料一致性校验报告.json"
    run_script("product_verify.py", ["--formal", str(publication_root), "--quality", str(paths.quality),
               "--facts", str(paths.work / "application-facts.json"), "--business", str(business),
               "--manual-content", str(manual_json),
               "--application-model", str(application_model), "--provenance", str(provenance),
               "--screenshot-index", str(screenshots), "--render-reports", str(render_dir), "--output", str(verification)], {0, 2})
    report_data = load_json(verification)
    (paths.quality / "材料一致性校验报告.md").write_text(markdown_report(report_data), encoding="utf-8")
    write_sha256s(paths.draft, paths.quality / "SHA256SUMS.txt")
    record_stage(paths, state, "verification", "complete", hash_inputs([verification]),
                 [verification, paths.quality / "材料一致性校验报告.md", paths.quality / "SHA256SUMS.txt"])
    existing_formal = [path for path in paths.formal.iterdir() if path.is_file()]
    if report_data.get("release_ready"):
        formal_snapshot = snapshot_files(paths, existing_formal, "正式资料被新版本替换")
        try:
            for path in list(paths.formal.iterdir()):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            for _, draft_file in publication:
                destination = paths.formal / draft_file.name
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                shutil.copy2(draft_file, temporary)
                os.replace(temporary, destination)
        except PermissionError as exc:
            # Reviewers usually keep the previous release open in Word/WPS
            # while regenerating; a locked file must not crash the workflow.
            locked = getattr(exc, "filename", None) or str(exc)
            (paths.quality / "待确认事项清单.md").write_text(
                "# 待确认事项清单\n\n"
                f"- 正式资料被其他程序占用（通常是 Word/WPS 正在打开）：{locked}\n"
                "- 请关闭该文件后重新运行，新版材料会自动完成替换；当前草稿与历史快照均已保留。\n",
                encoding="utf-8")
            record_stage(paths, state, "release", "blocked_by_locked_files",
                         hash_inputs([publication_root, paths.quality]),
                         [path for path in publication_root.iterdir() if path.is_file()],
                         f"交付文件被占用：{locked}")
            progress("正式资料正被其他程序打开，请关闭 Word/WPS 中的材料后重新运行")
            print("DRAFT_STATUS=locked_delivery_files")
            return 2
        record_stage(paths, state, "release", "complete", hash_inputs([paths.formal, paths.quality]),
                     [path for path in paths.formal.iterdir() if path.is_file()],
                     f"正式材料已通过阻塞项检查；上一版本：{formal_snapshot or '无'}")
        prune_delivery_root(paths)
        progress("已完成，可以检查并提交")
        print(f"FORMAL_OUTPUT={paths.formal}")
        return 0
    # A failed new draft must not withdraw a previously verified release.
    record_stage(paths, state, "release", "draft_blocked", hash_inputs([publication_root, paths.quality]),
                 [path for path in publication_root.iterdir() if path.is_file()],
                 f"截图状态：{screenshot_state}；正式发布门禁未通过")
    progress("草稿已生成，正式发布仍有阻塞项")
    print("DRAFT_STATUS=blocked")
    return 3 if screenshot_state == "skipped_by_user" else 2


def status(paths: ProductPaths) -> int:
    state = load_state(paths)
    print(json.dumps({"project": str(paths.project), "output": str(paths.root), "rules_snapshot": RULES_SNAPSHOT,
                      "stages": {name: item.get("status") for name, item in state.get("stages", {}).items()}}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "generate", "resume", "status", "rollback"])
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--intake", type=Path)
    parser.add_argument("--business", type=Path)
    args = parser.parse_args()
    paths = ProductPaths.create(args.project.resolve(), args.output)
    if args.action == "prepare":
        return prepare(paths)
    if args.action in {"generate", "resume"}:
        return generate(paths, args.intake, args.business)
    if args.action == "status":
        return status(paths)
    return run_script("rollback_release.py", ["--history", str(paths.history),
                                               "--formal", str(paths.formal)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
