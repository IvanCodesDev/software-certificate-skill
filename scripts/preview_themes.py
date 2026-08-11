#!/usr/bin/env python3
"""Render a black-white-gray software-copyright manual layout preview."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import load_json

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit("Pillow is required for layout previews.") from exc


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/simhei.ttf") if bold else Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc")
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def text(draw, xy, value, fill, size, bold=False, anchor=None):
    draw.text(xy, value, fill=fill, font=font(size, bold), anchor=anchor)


def preview(theme: dict, output: Path) -> None:
    colors = {key: rgb(value) for key, value in theme["colors"].items()}
    image = Image.new("RGB", (1240, 1754), colors["paper"])
    draw = ImageDraw.Draw(image)
    left, right = 145, 1095

    text(draw, (left, 95), "示例业务协同平台", colors["muted"], 18)
    text(draw, (right, 95), "V1.0", colors["muted"], 18, anchor="ra")
    draw.line((left, 132, right, 132), fill=colors["rule"], width=2)

    text(draw, (left, 210), "1.1 登录与工作台", colors["text"], 36, True)
    text(draw, (left, 285), "用户通过登录界面进入系统，并核对当前账号的角色与功能范围。", colors["text"], 24)

    steps = [
        "1. 打开系统登录页面，确认访问地址与软件名称。",
        "2. 输入测试账号和密码，单击“登录”按钮。",
        "3. 进入工作台后，核对角色、菜单范围及系统提示。"
    ]
    y = 365
    for item in steps:
        text(draw, (left + 10, y), item, colors["text"], 24)
        y += 66

    draw.rectangle((left, 585, right, 1085), fill=(250, 250, 250), outline=colors["rule"], width=2)
    text(draw, (620, 810), "真实系统界面截图", colors["muted"], 28, False, "mm")
    text(draw, (620, 1118), "图 1  登录成功后的工作台界面", colors["text"], 20, False, "mm")

    table_top = 1205
    col = [left, 380, 760, right]
    draw.rectangle((left, table_top, right, table_top + 72), fill=colors["panel"], outline=colors["rule"], width=2)
    for x in col[1:-1]:
        draw.line((x, table_top, x, table_top + 210), fill=colors["rule"], width=2)
    for y_line in (table_top + 72, table_top + 141, table_top + 210):
        draw.line((left, y_line, right, y_line), fill=colors["rule"], width=2)
    headers = [(left + 18, "检查项"), (col[1] + 18, "预期结果"), (col[2] + 18, "操作说明")]
    for x, label in headers:
        text(draw, (x, table_top + 22), label, colors["text"], 20, True)
    rows = [("账号状态", "登录成功", "显示当前用户"), ("权限范围", "菜单匹配", "核对可见功能")]
    for row_index, row in enumerate(rows):
        y_row = table_top + 91 + row_index * 69
        for x, value in zip((left + 18, col[1] + 18, col[2] + 18), row):
            text(draw, (x, y_row), value, colors["text"], 19)

    text(draw, (620, 1660), "第 6 页", colors["muted"], 18, False, "mm")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--themes", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    paths = sorted(args.themes.glob("*.json"))
    for path in paths:
        theme = load_json(path)
        output = args.output_dir / f"{theme['id']}.png"
        preview(theme, output)
        print(f"PREVIEW={output.resolve()}")
    print(f"COUNT={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
