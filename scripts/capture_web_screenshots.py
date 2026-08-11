#!/usr/bin/env python3
"""Capture deterministic, traceable web screenshots from a JSON plan."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from common import load_json, now_iso, save_json, sha256_file

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,79}$")
ACTION_TYPES = {
    "goto", "click", "fill", "press", "select", "check", "uncheck",
    "hover", "wait_for", "wait_ms", "scroll_into_view", "assert_text",
    "assert_visible",
}


def expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_environment(item) for key, item in value.items()}
    return value


def unresolved_environment_slots(value: Any) -> list[str]:
    text = json.dumps(value, ensure_ascii=False)
    return sorted(set(re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%", text)))


def plan_errors(plan: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    if plan.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not isinstance(plan.get("base_url"), str) or not plan.get("base_url", "").strip():
        errors.append("base_url is required")
    captures = plan.get("captures")
    if not isinstance(captures, list) or not captures:
        errors.append("captures must be a non-empty array")
        return errors
    seen: set[str] = set()
    for index, capture in enumerate(captures):
        prefix = f"captures[{index}]"
        if not isinstance(capture, dict):
            errors.append(f"{prefix} must be an object")
            continue
        shot_id = capture.get("id")
        if not isinstance(shot_id, str) or not ID_PATTERN.match(shot_id):
            errors.append(f"{prefix}.id must match {ID_PATTERN.pattern}")
        elif shot_id in seen:
            errors.append(f"duplicate capture id: {shot_id}")
        else:
            seen.add(shot_id)
        if not isinstance(capture.get("title"), str) or not capture.get("title", "").strip():
            errors.append(f"{prefix}.title is required")
        if not capture.get("url") and not capture.get("route"):
            errors.append(f"{prefix} requires url or route")
        if capture.get("full_page") and capture.get("selector"):
            errors.append(f"{prefix} must choose either full_page or selector")
        for section in ("actions", "assertions"):
            actions = capture.get(section, [])
            if not isinstance(actions, list):
                errors.append(f"{prefix}.{section} must be an array")
                continue
            for action_index, action in enumerate(actions):
                ap = f"{prefix}.{section}[{action_index}]"
                if not isinstance(action, dict):
                    errors.append(f"{ap} must be an object")
                    continue
                action_type = action.get("type")
                if action_type not in ACTION_TYPES:
                    errors.append(f"{ap}.type is unsupported: {action_type}")
                if action_type not in {"wait_ms", "goto"} and not action.get("selector"):
                    errors.append(f"{ap}.selector is required for {action_type}")
    browser = plan.get("browser", {})
    if browser.get("storage_state") and browser.get("user_data_dir"):
        errors.append("browser.storage_state and browser.user_data_dir are mutually exclusive")
    return errors


def output_path(plan: dict[str, Any], plan_path: Path, override: Path | None) -> Path:
    if override:
        return override.expanduser().resolve()
    configured = Path(plan.get("output_dir", "02-evidence/screenshots"))
    return configured.resolve() if configured.is_absolute() else (plan_path.parent / configured).resolve()


def wait_for_health(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.35)
    raise RuntimeError(f"health check timed out: {url}; last_error={last_error}")


@contextmanager
def managed_server(command: str | None, cwd: Path | None, health_url: str | None,
                   startup_timeout: float, log_dir: Path):
    if not command:
        if health_url:
            wait_for_health(health_url, startup_timeout)
        yield None
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "app.stdout.log"
    stderr_path = log_dir / "app.stderr.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        process = subprocess.Popen(
            command, cwd=str(cwd) if cwd else None, shell=True,
            stdout=out, stderr=err, creationflags=flags,
            start_new_session=os.name != "nt",
        )
        try:
            if health_url:
                wait_for_health(health_url, startup_timeout)
            elif process.poll() is not None:
                raise RuntimeError(f"start command exited early with {process.returncode}")
            else:
                time.sleep(1)
            yield process
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def image_metrics(path: Path) -> dict[str, Any]:
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        gray = rgb.convert("L")
        stat = ImageStat.Stat(gray)
        histogram = gray.histogram()
        pixels = max(1, gray.width * gray.height)
        entropy = 0.0
        for count in histogram:
            if count:
                probability = count / pixels
                entropy -= probability * math.log2(probability)
        white_pixels = sum(histogram[248:])
        dark_pixels = sum(histogram[:235])
        thumb = gray.resize((9, 8))
        values = list(thumb.getdata())
        bits = []
        for y in range(8):
            row = values[y * 9:(y + 1) * 9]
            bits.extend(row[x] > row[x + 1] for x in range(8))
        dhash = 0
        for bit in bits:
            dhash = (dhash << 1) | int(bit)
        return {
            "width": rgb.width, "height": rgb.height, "mode": image.mode,
            "grayscale_mean": round(float(stat.mean[0]), 3),
            "grayscale_stddev": round(float(stat.stddev[0]), 3),
            "entropy": round(entropy, 3),
            "near_white_ratio": round(white_pixels / pixels, 6),
            "content_ratio": round(dark_pixels / pixels, 6),
            "dhash": f"{dhash:016x}",
        }


def hamming_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def quality_findings(metrics: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    checks = (
        ("min_width", metrics["width"], quality.get("min_width", 900), "lt"),
        ("min_height", metrics["height"], quality.get("min_height", 500), "lt"),
        ("min_entropy", metrics["entropy"], quality.get("min_entropy", 0.8), "lt"),
        ("min_content_ratio", metrics["content_ratio"], quality.get("min_content_ratio", 0.002), "lt"),
        ("max_near_white_ratio", metrics["near_white_ratio"], quality.get("max_near_white_ratio", 0.997), "gt"),
    )
    for name, actual, threshold, direction in checks:
        failed = actual < threshold if direction == "lt" else actual > threshold
        if failed:
            findings.append({"code": name, "actual": actual, "threshold": threshold})
    return findings


def add_stability_style(page: Any) -> None:
    page.add_style_tag(content="""
        *, *::before, *::after {
          animation-duration: 0s !important; animation-delay: 0s !important;
          transition-duration: 0s !important; caret-color: transparent !important;
          scroll-behavior: auto !important;
        }
    """)
    page.evaluate("""async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      await Promise.all(Array.from(document.images).map(img => img.complete
        ? Promise.resolve() : new Promise(resolve => {
            img.addEventListener('load', resolve, {once:true});
            img.addEventListener('error', resolve, {once:true});
          })));
    }""")


def first_locator(page: Any, selector: str, timeout: int) -> Any:
    item = page.locator(selector).first
    item.wait_for(state="attached", timeout=timeout)
    return item


def perform_action(page: Any, action: dict[str, Any], base_url: str, default_timeout: int) -> None:
    action_type = action["type"]
    timeout = int(action.get("timeout_ms", default_timeout))
    if action_type == "wait_ms":
        page.wait_for_timeout(int(action.get("milliseconds", action.get("value", 300))))
        return
    if action_type == "goto":
        target = action.get("url") or action.get("route")
        page.goto(urljoin(base_url.rstrip("/") + "/", str(target).lstrip("/")),
                  wait_until=action.get("wait_until", "domcontentloaded"), timeout=timeout)
        return
    item = first_locator(page, action["selector"], timeout)
    if action_type == "click":
        item.click(timeout=timeout)
    elif action_type == "fill":
        item.fill(str(action.get("value", "")), timeout=timeout)
    elif action_type == "press":
        item.press(str(action["key"]), timeout=timeout)
    elif action_type == "select":
        item.select_option(action.get("value"), timeout=timeout)
    elif action_type == "check":
        item.check(timeout=timeout)
    elif action_type == "uncheck":
        item.uncheck(timeout=timeout)
    elif action_type == "hover":
        item.hover(timeout=timeout)
    elif action_type == "wait_for":
        item.wait_for(state=action.get("state", "visible"), timeout=timeout)
    elif action_type == "scroll_into_view":
        item.scroll_into_view_if_needed(timeout=timeout)
    elif action_type == "assert_text":
        actual = item.inner_text(timeout=timeout)
        expected = str(action.get("contains", action.get("value", "")))
        if expected not in actual:
            raise AssertionError(f"selector {action['selector']} lacks expected text: {expected}")
    elif action_type == "assert_visible" and not item.is_visible(timeout=timeout):
        raise AssertionError(f"selector is not visible: {action['selector']}")


def shot_path(output: Path, order: int, shot_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", shot_id).strip("-.")
    return output / f"{order:03d}-{safe}.png"


def merge_evidence_graph(source: Path, output: Path, records: list[dict[str, Any]]) -> None:
    graph = load_json(source)
    nodes = [node for node in graph.get("nodes", []) if not str(node.get("id", "")).startswith("SHOT-")]
    edges = [edge for edge in graph.get("edges", []) if not str(edge.get("from", "")).startswith("SHOT-")]
    for record in records:
        if record.get("status") not in {"pass", "quality_warning"}:
            continue
        shot_id = f"SHOT-{record['id']}"
        nodes.append({
            "id": shot_id, "type": "screenshot", "name": record["title"],
            "status": "captured", "path": record["path"], "sha256": record["sha256"],
            "route": record["url"], "role": record.get("role"),
            "captured_at": record["captured_at"], "metrics": record["metrics"],
        })
        for evidence_id in record.get("evidence_ids", []):
            edges.append({"from": shot_id, "to": evidence_id, "relation": "supports"})
    graph["nodes"] = nodes
    graph["edges"] = edges
    graph.setdefault("summary", {})["captured_screenshots"] = sum(
        node.get("type") == "screenshot" for node in nodes
    )
    graph["summary"]["screenshot_capture_updated_at"] = now_iso()
    save_json(output, graph)


def run_capture(plan: dict[str, Any], plan_path: Path, output: Path,
                fail_fast: bool, allow_quality_warnings: bool,
                evidence_source: Path | None, evidence_output: Path | None) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Run: python -m pip install 'playwright>=1.49,<2'; "
            "python -m playwright install chromium"
        ) from exc

    output.mkdir(parents=True, exist_ok=True)
    browser_cfg = plan.get("browser", {})
    quality = dict(plan.get("quality", {}))
    base_url = plan["base_url"]
    default_timeout = int(plan.get("default_timeout_ms", 15000))
    server = plan.get("server", {})
    server_cwd = server.get("cwd")
    server_cwd_path = None
    if server_cwd:
        server_cwd_path = Path(server_cwd)
        if not server_cwd_path.is_absolute():
            server_cwd_path = (plan_path.parent / server_cwd_path).resolve()
    records: list[dict[str, Any]] = []
    previous: list[dict[str, Any]] = []
    runtime = {"console": [], "page_errors": [], "request_failures": []}

    with managed_server(server.get("command"), server_cwd_path,
                        server.get("health_url", base_url),
                        float(server.get("startup_timeout_seconds", 60)), output / "logs"):
        with sync_playwright() as playwright:
            engine = getattr(playwright, browser_cfg.get("engine", "chromium"))
            launch_options: dict[str, Any] = {"headless": bool(browser_cfg.get("headless", True))}
            for key in ("channel", "executable_path"):
                if browser_cfg.get(key):
                    launch_options[key] = browser_cfg[key]
            browser = None
            if browser_cfg.get("user_data_dir"):
                context = engine.launch_persistent_context(
                    str(Path(browser_cfg["user_data_dir"]).expanduser()),
                    viewport=browser_cfg.get("viewport", {"width": 1440, "height": 900}),
                    device_scale_factor=float(browser_cfg.get("device_scale_factor", 1)),
                    locale=browser_cfg.get("locale", "zh-CN"),
                    color_scheme=browser_cfg.get("color_scheme", "light"),
                    reduced_motion="reduce", **launch_options,
                )
            else:
                browser = engine.launch(**launch_options)
                context_options: dict[str, Any] = {
                    "viewport": browser_cfg.get("viewport", {"width": 1440, "height": 900}),
                    "device_scale_factor": float(browser_cfg.get("device_scale_factor", 1)),
                    "locale": browser_cfg.get("locale", "zh-CN"),
                    "color_scheme": browser_cfg.get("color_scheme", "light"),
                    "reduced_motion": "reduce",
                    "ignore_https_errors": bool(browser_cfg.get("ignore_https_errors", False)),
                }
                if browser_cfg.get("timezone_id"):
                    context_options["timezone_id"] = browser_cfg["timezone_id"]
                if browser_cfg.get("storage_state"):
                    state_path = Path(browser_cfg["storage_state"])
                    if not state_path.is_absolute():
                        state_path = (plan_path.parent / state_path).resolve()
                    context_options["storage_state"] = str(state_path)
                context = browser.new_context(**context_options)
            context.set_default_timeout(default_timeout)
            page = context.pages[0] if context.pages else context.new_page()
            page.on("console", lambda msg: runtime["console"].append({"type": msg.type, "text": msg.text}))
            page.on("pageerror", lambda exc: runtime["page_errors"].append(str(exc)))
            page.on("requestfailed", lambda req: runtime["request_failures"].append(
                {"url": req.url, "method": req.method, "failure": req.failure}
            ))
            for action in plan.get("setup", []):
                perform_action(page, action, base_url, default_timeout)

            for order, capture in enumerate(plan["captures"], 1):
                target = capture.get("url") or capture.get("route")
                target_url = urljoin(base_url.rstrip("/") + "/", str(target).lstrip("/"))
                path = shot_path(output, order, capture["id"])
                starts = {key: len(value) for key, value in runtime.items()}
                record: dict[str, Any] = {
                    "id": capture["id"], "title": capture["title"],
                    "role": capture.get("role"), "evidence_ids": capture.get("evidence_ids", []),
                    "requested_url": target_url, "captured_at": now_iso(),
                }
                try:
                    response = page.goto(
                        target_url, wait_until=capture.get("wait_until", "domcontentloaded"),
                        timeout=int(capture.get("navigation_timeout_ms", default_timeout)),
                    )
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=int(capture.get("network_idle_timeout_ms", 5000))
                        )
                    except Exception:
                        record["network_idle_timeout"] = True
                    if capture.get("ready_selector"):
                        page.locator(capture["ready_selector"]).first.wait_for(
                            state="visible", timeout=int(capture.get("ready_timeout_ms", default_timeout))
                        )
                    for action in capture.get("actions", []):
                        perform_action(page, action, base_url, default_timeout)
                    for assertion in capture.get("assertions", []):
                        perform_action(page, assertion, base_url, default_timeout)
                    add_stability_style(page)
                    page.wait_for_timeout(int(capture.get("settle_ms", 250)))
                    hidden: list[tuple[Any, str | None]] = []
                    for selector in capture.get("hide", []):
                        for item in page.locator(selector).all():
                            original = item.get_attribute("style")
                            item.evaluate("el => el.style.visibility = 'hidden'")
                            hidden.append((item, original))
                    masks = [page.locator(selector) for selector in capture.get("mask", [])]
                    shot_options: dict[str, Any] = {
                        "path": str(path), "animations": "disabled", "caret": "hide",
                        "mask": masks, "mask_color": capture.get("mask_color", "#808080"),
                    }
                    if capture.get("selector"):
                        item = page.locator(capture["selector"]).first
                        item.scroll_into_view_if_needed()
                        item.screenshot(**shot_options)
                    else:
                        shot_options["full_page"] = bool(capture.get("full_page", False))
                        page.screenshot(**shot_options)
                    for item, original in hidden:
                        if original is None:
                            item.evaluate("el => el.removeAttribute('style')")
                        else:
                            item.evaluate("(el, value) => el.setAttribute('style', value)", original)
                    metrics = image_metrics(path)
                    findings = quality_findings(metrics, {**quality, **capture.get("quality", {})})
                    for prior in previous:
                        same_size = (metrics["width"], metrics["height"]) == (
                            prior["metrics"]["width"], prior["metrics"]["height"]
                        )
                        if same_size:
                            distance = hamming_hex(metrics["dhash"], prior["metrics"]["dhash"])
                            if distance <= int(quality.get("duplicate_dhash_distance", 1)):
                                if not capture.get("allow_duplicate", False):
                                    findings.append({"code": "near_duplicate", "duplicate_of": prior["id"],
                                                     "dhash_distance": distance})
                                break
                    record.update({
                        "status": "quality_warning" if findings else "pass",
                        "path": str(path), "sha256": sha256_file(path), "url": page.url,
                        "document_title": page.title(), "http_status": response.status if response else None,
                        "metrics": metrics, "quality_findings": findings,
                    })
                    previous.append(record)
                except Exception as exc:
                    failure_path = output / f"{order:03d}-{capture['id']}.failed.png"
                    try:
                        page.screenshot(path=str(failure_path), full_page=False, animations="disabled")
                    except Exception:
                        failure_path = None
                    record.update({
                        "status": "error", "error_type": type(exc).__name__, "error": str(exc),
                        "url": page.url, "failure_screenshot": str(failure_path) if failure_path else None,
                    })
                for key in runtime:
                    record[key] = runtime[key][starts[key]:]
                records.append(record)
                save_json(output / "screenshot-index.json", {
                    "schema_version": "1.0", "generated_at": now_iso(),
                    "plan": str(plan_path), "base_url": base_url, "captures": records,
                })
                if fail_fast and record["status"] != "pass":
                    break

            if browser_cfg.get("save_storage_state"):
                state_path = Path(browser_cfg["save_storage_state"])
                if not state_path.is_absolute():
                    state_path = (plan_path.parent / state_path).resolve()
                state_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_path))
            context.close()
            if browser:
                browser.close()

    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "plan": str(plan_path),
        "base_url": base_url, "output_dir": str(output), "captures": records,
        "summary": {
            "requested": len(plan["captures"]), "completed": len(records),
            "passed": sum(r["status"] == "pass" for r in records),
            "quality_warnings": sum(r["status"] == "quality_warning" for r in records),
            "errors": sum(r["status"] == "error" for r in records),
        },
    }
    save_json(output / "screenshot-index.json", report)
    if evidence_source and evidence_output:
        merge_evidence_graph(evidence_source, evidence_output, records)
    if report["summary"]["errors"]:
        return 2
    if report["summary"]["quality_warnings"] and not allow_quality_warnings:
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--allow-quality-warnings", action="store_true")
    parser.add_argument("--evidence-source", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan = load_json(plan_path)
    errors = plan_errors(plan)
    if errors:
        print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    output = output_path(plan, plan_path, args.output)
    if args.validate_only:
        print(json.dumps({
            "status": "valid", "captures": len(plan["captures"]),
            "output_dir": str(output), "playwright_required_for_capture": True,
        }, ensure_ascii=False, indent=2))
        return 0
    plan = expand_environment(plan)
    unresolved = unresolved_environment_slots(plan)
    if unresolved:
        print(json.dumps({"status": "missing_environment", "slots": unresolved},
                         ensure_ascii=False, indent=2))
        return 1
    if bool(args.evidence_source) != bool(args.evidence_output):
        parser.error("--evidence-source and --evidence-output must be used together")
    return run_capture(plan, plan_path, output, args.fail_fast,
                       args.allow_quality_warnings, args.evidence_source, args.evidence_output)


if __name__ == "__main__":
    raise SystemExit(main())
