"""Glaucous 天青夏日主题：色板常量 + rich Theme + Console 工厂（任务 3.1/3.7，FR-30）。

设计要点（Day5 Plan §4.1）：
- 色板单一出口：全仓引用 PALETTE，禁止字面色值散落（主题文档 §1 取色）；
- rich Theme 命名风格为唯一样式构造点（build_theme）；
- 终端降级走 make_console() 工厂：非 TTY / NO_COLOR → 纯文本；
  GLAUCOUS_COLOR 显式覆盖；其余由 rich 自动探测（truecolor/256/16 三档，
  概设 §8.5），本层不重复造映射表。
"""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.style import Style
from rich.theme import Theme

# ---------------------------------------------------------------------------
# 品牌色板（主题文档 §1：雨过天青的晴空 + 海鸥掠过 + 夏日海风）
# ---------------------------------------------------------------------------

PALETTE = {
    "brand": "#3AA6B9",    # 天青主色：标题、强调、⏺ 动作符、Plan 徽标
    "text": "#EAF4F4",     # 海鸥白：正文、高对比文字
    "bg": "#1B2A4A",       # 深海蓝：背景基底（终端背景由终端自身提供，此处留档）
    "dim": "#9BD1D9",      # 海盐青：次级文字、卡片描边、思考流
    "warn": "#F4A261",     # 晚霞橙：警告、审批/确认（需人介入时）
    "muted": "#5A7A8C",    # 晴空灰：占位、禁用、弱化
    "success": "#7FB685",  # 海草绿：成功（清爽，不带警示感）
    "error": "#E07A5F",    # 陶土红：错误、破坏性警示（暖色里最克制的红）
    "accent": "#6BB7C9",   # 亮青：可交互元素、工具名、Build 徽标
}

# 占用条三档配色（与压缩阈值同一档位判定，FR-25/30）：低绿 / 中橙 / 高红
LEVEL_STYLE = {"low": "glaucous.success", "warn": "glaucous.warn", "critical": "glaucous.error"}


def build_theme() -> Theme:
    """rich Theme 唯一构造点：命名风格一律 `glaucous.*` 前缀。"""
    return Theme(
        {
            "glaucous.brand": Style(color=PALETTE["brand"], bold=True),
            "glaucous.text": Style(color=PALETTE["text"]),
            "glaucous.dim": Style(color=PALETTE["dim"]),
            "glaucous.muted": Style(color=PALETTE["muted"]),
            "glaucous.warn": Style(color=PALETTE["warn"]),
            "glaucous.error": Style(color=PALETTE["error"]),
            "glaucous.success": Style(color=PALETTE["success"]),
            "glaucous.accent": Style(color=PALETTE["accent"]),
            # Panel 描边用：审批/提问/方案卡片的默认边色
            "glaucous.card": Style(color=PALETTE["dim"]),
        }
    )


# GLAUCOUS_COLOR 允许值 → rich color_system 映射（rich 无 "16"，16 色即 standard）
_COLOR_SYSTEM_MAP = {
    "truecolor": "truecolor",
    "256": "256",
    "16": "standard",
    "standard": "standard",
    "mono": "mono",
}


def make_console() -> Console:
    """Console 工厂：终端能力探测与降级的单一出口（任务 3.7，概设 §8.5）。

    降级顺序：
    1. 非 TTY（重定向/管道）或 NO_COLOR → 纯文本（no_color），日志干净；
    2. GLAUCOUS_COLOR 显式指定（truecolor/256/16/mono）→ 覆盖探测；
    3. 其余由 rich 自动探测：真彩原色 / 256 映射最近调色板 / 16 色映射基色。
    """
    # Windows TTY：先启用 conhost 的 VT 处理（无害空命令）——否则 rich 探测为
    # legacy_windows 渲染路径，经 Win32 API 直写时绕过 stdout 的 errors="replace"
    # 兜底，意象符（☁/❄/🌅）会 UnicodeEncodeError 崩溃（FR-34 降级不崩溃）；
    # 启用后 detect_legacy_windows 为 False，rich 走 VT 转义序列路径，
    # 与 Windows Terminal/WSL 行为一致
    if sys.platform == "win32" and sys.stdout.isatty():
        os.system("")
    kwargs: dict = {"theme": build_theme(), "highlight": False}
    # 降级分支也须携带 theme：命名风格（glaucous.*）在 no_color 下不施加颜色，
    # 但 get_style 仍需能解析到条目，否则渲染时 MissingStyle 崩溃
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR", "").strip():
        return Console(no_color=True, highlight=False, theme=build_theme())
    override = os.environ.get("GLAUCOUS_COLOR", "").strip().lower()
    if override:
        color_system = _COLOR_SYSTEM_MAP.get(override)
        if color_system == "mono":
            return Console(no_color=True, highlight=False, theme=build_theme())
        if color_system is not None:
            kwargs["color_system"] = color_system
    return Console(**kwargs)
