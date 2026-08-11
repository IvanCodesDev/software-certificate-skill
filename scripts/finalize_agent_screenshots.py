#!/usr/bin/env python3
"""Validate screenshots produced by Computer Use and write a release-grade index."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Any

from capture_web_screenshots import image_metrics, merge_evidence_graph, quality_findings
from common import load_json, now_iso, save_json, sha256_file


SESSION_SCHEMA = Path(__file__).resolve().parents[1] / "assets/schemas/computer-use-session.schema.json"


def contract_errors(session: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(load_json(SESSION_SCHEMA))
        return [f"{'/'.join(map(str, error.path)) or '$'}: {error.message}" for error in validator.iter_errors(session)]
    except ImportError:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--evidence-source", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    args = parser.parse_args()
    plan_path, session_path = args.plan.resolve(), args.session.resolve()
    plan, session = load_json(plan_path), load_json(session_path)
    output, report_path = args.output.resolve(), args.report.resolve()
    output.mkdir(parents=True, exist_ok=True)
    planned = plan.get("captures", [])
    validation_errors = contract_errors(session)
    planned_ids = [item.get("id") for item in planned]
    if not planned:
        validation_errors.append("plan.captures must be non-empty")
    if len(planned_ids) != len(set(planned_ids)):
        validation_errors.append("plan capture ids must be unique")
    if bool(args.evidence_source) != bool(args.evidence_output):
        parser.error("--evidence-source and --evidence-output must be used together")
    supplied_items = [item for item in session.get("captures", []) if item.get("id")]
    supplied = {item.get("id"): item for item in supplied_items}
    supplied_counts = {shot_id: sum(item.get("id") == shot_id for item in supplied_items) for shot_id in supplied}
    actions = session.get("actions", [])
    records: list[dict[str, Any]] = []
    for order, item in enumerate(planned, 1):
        shot_id = item.get("id")
        source_item = supplied.get(shot_id, {})
        source_value = source_item.get("source_path") or source_item.get("path")
        source = Path(source_value).expanduser() if source_value else None
        if source and not source.is_absolute():
            source = (session_path.parent / source).resolve()
        record: dict[str, Any] = {
            "id": shot_id, "title": item.get("title"),
            "role": source_item.get("role") or item.get("role"),
            "url": source_item.get("url") or item.get("url") or item.get("route"),
            "evidence_ids": item.get("evidence_ids", []),
            "captured_at": source_item.get("captured_at") or session.get("completed_at"),
            "agent_mode": "computer_use",
        }
        findings: list[dict[str, Any]] = []
        action_records = [action for action in actions if action.get("capture_id") == shot_id]
        if not action_records or any(action.get("result") != "pass" for action in action_records):
            findings.append({"code": "action_receipt_missing_or_failed"})
        if session.get("launch", {}).get("result") != "pass":
            findings.append({"code": "application_launch_not_verified"})
        if session.get("login", {}).get("required") and session.get("login", {}).get("result") != "pass":
            findings.append({"code": "login_not_verified"})
        if not record.get("captured_at"):
            findings.append({"code": "capture_timestamp_missing"})
        if supplied_counts.get(shot_id, 0) != 1:
            findings.append({"code": "capture_receipt_duplicate"})
        if not source or not source.is_file():
            findings.append({"code": "source_file_missing"})
        else:
            safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(shot_id)).strip("-.") or f"shot-{order:03d}"
            target = output / f"{order:03d}-{safe_id}{source.suffix.lower()}"
            shutil.copy2(source, target)
            try:
                metrics = image_metrics(target)
                findings.extend(quality_findings(metrics, {
                    "min_width": 900, "min_height": 500, "min_entropy": 0.8,
                    "min_content_ratio": 0.002, "max_near_white_ratio": 0.997,
                    **plan.get("quality", {}), **item.get("quality", {}),
                }))
                record.update({"path": str(target), "sha256": sha256_file(target), "metrics": metrics})
            except Exception as exc:
                findings.append({"code": "image_decode_failed", "detail": str(exc)})
        if not record.get("role") or not record.get("url"):
            findings.append({"code": "context_missing"})
        if not record.get("evidence_ids"):
            findings.append({"code": "evidence_mapping_missing"})
        record["quality_findings"] = findings
        record["status"] = "pass" if not findings else "error"
        records.append(record)
    missing = sorted(set(supplied) - {item.get("id") for item in planned})
    passed = sum(item["status"] == "pass" for item in records)
    state = "captured" if planned and passed == len(planned) and not missing and not validation_errors else "failed"
    report = {
        "schema_version": "1.0", "generated_at": now_iso(),
        "mode": "computer_use", "state": state,
        "plan": str(plan_path), "session": str(session_path),
        "execution": {
            "launch": session.get("launch", {}), "login": session.get("login", {}),
            "action_count": len(actions), "started_at": session.get("started_at"),
            "completed_at": session.get("completed_at"),
        },
        "contract_errors": validation_errors,
        "captures": records,
        "summary": {
            "requested": len(planned), "completed": len(records), "passed": passed,
            "quality_warnings": 0, "errors": len(records) - passed,
            "missing_planned": max(0, len(planned) - passed), "unplanned_files": missing,
        },
    }
    save_json(report_path, report)
    if args.evidence_source and args.evidence_output:
        merge_evidence_graph(args.evidence_source.resolve(), args.evidence_output.resolve(), records)
    print(f"SCREENSHOT_INDEX={report_path}")
    print(f"MODE=computer_use STATE={state} PASSED={passed} REQUESTED={len(planned)}")
    return 0 if state == "captured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
