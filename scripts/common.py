#!/usr/bin/env python3
"""Shared, dependency-free helpers for software-certificate-skill."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SLOT_PATTERN = re.compile(r"【待申请人确认：[^】]+】")


def configure_utf8_stdio() -> None:
    """Keep Chinese progress output deterministic on English Windows runners."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def utf8_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


configure_utf8_stdio()


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


# Server-side implementation fingerprints shared by the project analyzer
# (architecture scope) and the source selector (side attribution and
# ownership review), so both stages agree on what counts as backend code.
BACKEND_ONLY_EXTENSIONS = {".java", ".kt", ".go", ".php", ".cs", ".rb", ".sql"}
BACKEND_IMPORTS = re.compile(
    r"(?m)^\s*(?:"
    # Python
    r"(?:from|import)\s+(?:flask\w*|django\w*|fastapi|sanic|tornado|aiohttp|bottle|starlette|"
    r"litestar|sqlalchemy|sqlmodel|peewee|tortoise|pymysql|psycopg2?|pymongo|celery)\b"
    # Node / TypeScript
    r"|.*require\(['\"](?:express|koa|fastify|restify|egg|mongoose|sequelize|typeorm|knex|"
    r"mysql2?|pg|mongodb|ioredis)['\"]\)"
    r"|import\s+.*from\s+['\"](?:express|koa|fastify|restify|egg|mongoose|sequelize|typeorm|"
    r"knex|mysql2?|pg|mongodb|ioredis|@nestjs/|@prisma/|@midwayjs/|@hapi/)"
    # Java / Kotlin: web, RPC, ORM and middleware clients
    r"|import\s+(?:static\s+)?(?:org\.springframework|javax\.servlet|jakarta\.servlet|"
    r"javax\.persistence|jakarta\.persistence|org\.hibernate|org\.mybatis|tk\.mybatis|"
    r"org\.apache\.ibatis|org\.apache\.dubbo|com\.alibaba\.dubbo|org\.elasticsearch|"
    r"redis\.clients|org\.redisson|com\.rabbitmq|org\.apache\.rocketmq|org\.apache\.kafka|"
    r"org\.quartz|io\.seata|feign\.|org\.apache\.shiro|io\.jsonwebtoken)\b"
    # Go
    r"|\"(?:net/http|database/sql|github\.com/gin-gonic/gin|github\.com/labstack/echo"
    r"|github\.com/gofiber/fiber|gorm\.io/gorm|go\.mongodb\.org/mongo-driver)"
    # PHP / C# / Ruby
    r"|use\s+(?:Illuminate|Symfony|Laravel|Hyperf|Think)\\\\"
    r"|using\s+(?:Microsoft\.AspNetCore|Microsoft\.EntityFrameworkCore|System\.Web|System\.Data\.SqlClient)\b"
    r"|require\s+['\"](?:rails|sinatra|active_record|grape)"
    r")")
# Directories that hold the server half of a fullstack repo. Generic names
# like "api" or "service" stay out: frontend code uses them for HTTP-client
# wrappers, so they would misattribute the frontend side.
BACKEND_SIDE_DIRS = {"server", "backend", "srv", "api-server", "server-api",
                     "backend-api", "server-side", "serverside"}
# Extensions whose side is ambiguous: attribution needs imports or location.
AMBIGUOUS_SIDE_EXTENSIONS = {".py", ".js", ".ts", ".mjs", ".cjs"}


def under_backend_dir(relative: str) -> bool:
    """True when the file lives inside a recognised server-side directory."""
    return any(part.lower() in BACKEND_SIDE_DIRS for part in Path(relative).parts[:-1])


def looks_like_backend(relative: str, text: str) -> bool:
    """True when one source file reads as a server-side implementation."""
    suffix = Path(relative).suffix.lower()
    if suffix in BACKEND_ONLY_EXTENSIONS:
        return True
    return suffix in AMBIGUOUS_SIDE_EXTENSIONS and bool(BACKEND_IMPORTS.search(text))


def strongly_backend(relative: str, text: str) -> bool:
    """True when a file is attributable to a real server implementation.

    Stricter than :func:`looks_like_backend`, which accepts any backend-only
    extension: a couple of vendored ``.java`` snippets inside a frontend
    repository must stay weak evidence. Web/RPC/ORM imports or a server-side
    directory, by contrast, mean the repository actually hosts the server.
    """
    return under_backend_dir(relative) or bool(BACKEND_IMPORTS.search(text))


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
