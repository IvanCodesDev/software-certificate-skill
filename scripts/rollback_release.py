#!/usr/bin/env python3
"""Restore the most recent saved formal release snapshot."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import load_json, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="软件著作权申请资料目录")
    args = parser.parse_args()
    root = args.output.resolve()
    history, formal = root / "历史版本", root / "正式资料"
    candidates = sorted((path for path in history.iterdir() if (path / "snapshot.json").is_file()), reverse=True) if history.exists() else []
    if not candidates:
        print("ROLLBACK=NO_SNAPSHOT")
        return 2
    snapshot = candidates[0]
    model = load_json(snapshot / "snapshot.json")
    restored = []
    for item in model.get("files", []):
        source = Path(item["backup"])
        destination = formal / Path(item["source"]).name
        if source.is_file() and sha256_file(source) == item["sha256"]:
            shutil.copy2(source, destination)
            restored.append(destination.name)
    print(f"ROLLBACK={snapshot}")
    print(f"RESTORED={len(restored)} {'|'.join(restored)}")
    return 0 if restored else 2


if __name__ == "__main__":
    raise SystemExit(main())
