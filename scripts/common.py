#!/usr/bin/env python3
"""Shared, dependency-free helpers for software-certificate-skill."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SLOT_PATTERN = re.compile(r"【待申请人确认：[^】]+】")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def stable_id(prefix: str, value: str, length: int = 12) -> str:
    return f"{prefix}-{sha256_text(value)[:length]}"


def find_slots(value: Any) -> list[str]:
    return sorted(set(SLOT_PATTERN.findall(json.dumps(value, ensure_ascii=False))))


def safe_text(path: Path, max_bytes: int = 2_000_000) -> str | None:
    if path.stat().st_size > max_bytes:
        return None
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return None


def normalize_display_name(stem: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    words = re.sub(r"[_\-.]+", " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    return words or stem


def unique_in_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
