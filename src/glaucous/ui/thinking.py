"""思考过程动态区与用量行格式化（自 cli.py 拆出，v1.1 评审重构）。

ThinkingView：ANSI 擦除重绘协议的自管动态区（v1.1 修订，取代 rich.live.Live）；
用量格式化（_fmt_tokens/_usage_line/_usage_token_brief）为 repl 轮末与折叠
摘要行同源的 token 口径（turn_usage 本轮累计）。
"""

from __future__ import annotations

from typing import Any

from rich.cells import chop_cells

from ..theme import console, escape
from .render_events import _thinking_line

# 思考区动态区高度下限（行）；实际窗口随终端高度自适应（v1.1 修订：生成期间
# 尽量不截断思考内容，轮末统一收缩——下限兜底矮终端，上限 60 防占满屏）
THINKING_MAX_LINES = 8

# 动态区正文尾行单行宽度上限（F4 §4.2：长段落折行单行，防止窗口被单行淹没）
THINKING_LINE_WIDTH = 120


def _thinking_window() -> int:
    """动态区滚动窗口高度：随终端高度自适应（下限 8，上限 60）。"""
    height = getattr(console, "height", None) or 24
    return max(THINKING_MAX_LINES, min(height - 12, 60))


def _fmt_tokens(n: int) -> str:
    """用量数值格式：<1000 原样，≥1000 保留一位小数加 k（v1.1 R5）。"""
    return str(n) if n < 1000 else f"{n / 1000:.1f}k"


def _usage_line(usage: dict[str, Any]) -> str | None:
    """轮末用量行：⏱ ↑12.3k ↓456 tokens · 缓存命中 82%（v1.1 R5）。

    本轮累计口径（turn_usage）；无任何 prompt/completion 数据返回 None（不打印）；
    cache_hit 为 None（供应商无缓存字段）时省略缓存段（§5.3 不变量）。
    """
    prompt, completion = usage.get("prompt", 0), usage.get("completion", 0)
    if not prompt and not completion:
        return None
    line = f"⏱ ↑{_fmt_tokens(prompt)} ↓{_fmt_tokens(completion)} tokens"
    hit, miss = usage.get("cache_hit"), usage.get("cache_miss")
    if hit is not None:
        total = hit + (miss or 0)
        rate = round(hit * 100 / total) if total > 0 else 0
        line += f" · 缓存命中 {rate}%"
    return line


def _usage_token_brief(usage: dict[str, Any]) -> str:
    """折叠摘要行的 token 段（与用量行同源：turn_usage 累计，两处数字一致）。"""
    prompt, completion = usage.get("prompt", 0), usage.get("completion", 0)
    if not prompt and not completion:
        return ""
    return f" · ↑{_fmt_tokens(prompt)} ↓{_fmt_tokens(completion)} tokens"


def _clip_line(line: str, max_width: int) -> str:
    """按显示宽度截为单行（CJK 占 2 格；折行会破坏动态区擦除行数协议）。"""
    cells = chop_cells(line, max(1, max_width))
    return cells[0] if cells else ""


class ThinkingView:
    """思考过程动态区（v1.1 修订：ANSI 擦除重绘协议，取代 rich.live.Live）。

    Live 在「自适应大窗口 + 中途 pause/resume + console 直打交叉」下重绘协议
    会崩坏：内容碎片化泄漏、事件退化为直打、计数丢失（用户 WSL 实测复现），
    改用与 select_with_arrows 同款的自管协议：每次事件后光标上移擦除旧块重绘，
    行为完全可控且已在真实终端验证。
    - pause/resume（live_hooks 接线）：阻塞交互（审批/提问/方案卡）与 diagnostic
      直打前擦除动态区让位，返回后重绘——交互卡打在动态区原位，不再交叉；
    - close：擦除动态区，原地收缩为一行摘要；
    - 窗口高度随终端自适应（_thinking_window：下限 8，上限 60），生成期间
      尽量不截断，轮末统一收缩；
    - 打印/擦除失败自动降级：事件改直打（paused 置位），不阻断会话。
    """

    def __init__(self) -> None:
        self.count = 0
        self._lines: list[str] = []
        self._text_buf = ""  # 正文增量滚动缓冲（F4 §4.2，仅尾部两行进窗口）
        self._drawn = False       # 动态区块当前在屏上（擦除行数依据）
        self._last_block = 0      # 上次绘制的块行数
        self._paused = False
        self.was_active = False   # 本轮曾进入动态区渲染（终答呈现路径判据）

    @property
    def active(self) -> bool:
        """折叠收纳判据（make_on_event）：本轮已激活且未被暂停（降级/让位时事件直打）。"""
        return self.was_active and not self._paused

    def start(self) -> None:
        self.was_active = True

    def _erase(self) -> None:
        """擦除屏上动态区块（光标回块首 + 清屏到底）；无块则空操作。"""
        if self._drawn:
            try:
                console.file.write(f"\x1b[{self._last_block}A\x1b[J")
                console.file.flush()
            except Exception:  # noqa: BLE001 —— 擦除失败按无块处理，不让位失败阻断会话
                pass
        self._drawn = False
        self._last_block = 0

    def _redraw(self) -> None:
        """重绘动态区（暂停/降级时空操作）；打印失败置 paused 降级直打。"""
        if self._paused:
            return
        self._erase()
        lines = self._block_lines()
        width = max(console.width - 4, 20)  # 预留 2 格缩进，防折行破坏行数协议
        try:
            console.print(f"[glaucous.sub]  {_clip_line(lines[0], width)}[/]")
            for line in lines[1:]:
                console.print(f"[glaucous.dim]  {_clip_line(line, width)}[/]")
        except Exception:  # noqa: BLE001 —— 终端不支持/写入失败：降级实时打印
            self._paused = True
            return
        self._drawn = True
        self._last_block = len(lines)

    def _block_lines(self) -> list[str]:
        header = f"⚙ 思考中 · {self.count} 步"
        text_tail = []
        if self._text_buf:
            text_tail = [
                line if len(line) <= THINKING_LINE_WIDTH else line[:THINKING_LINE_WIDTH] + "…"
                for line in self._text_buf.split("\n")[-2:]
            ]
        window = _thinking_window()
        recent = (self._lines[-(window - len(text_tail)):] + text_tail)[-window:]
        return [header] + recent

    def add(self, event: str, payload: dict[str, Any]) -> None:
        self.count += 1
        line = _thinking_line(event, payload)
        if not self.active:  # 降级/暂停：实时直打摘要行
            console.print(f"[glaucous.dim]  {escape(line)}[/]")
            return
        self._lines.append(line)
        self._redraw()

    def add_text(self, delta: str) -> None:
        """正文增量进动态区滚动（F4 §4.2：生成期间允许临时泄露，轮末收缩折叠）。

        缓冲尾部两行进滚动窗口，视觉等同流式生成中；不计数（N 口径 = 非 text
        事件 + 交互伪事件 + 正文段落账条目，不含增量，§4.3）。
        """
        self._text_buf += delta
        if self.active:
            self._redraw()

    def start_turn(self) -> None:
        """轮级状态重置（F4 §4.3）：计数清零、滚动行与正文尾清空。

        会话缓冲不在此列（session_events 仅 /clear、/resume 由命令层清空）。"""
        self.count = 0
        self._lines.clear()
        self._text_buf = ""
        self.was_active = False
        self._paused = False
        self._drawn = False
        self._last_block = 0

    def note_step(self) -> None:
        """交互伪事件计数（不占动态区行）：交互以卡片形式呈现，但 N 口径需含（§3.1：
        N = 非 text 事件 + 交互伪事件，与缓冲//expand 同一口径）。经 live_hooks["step"] 接线。"""
        self.count += 1

    def pause(self) -> None:
        # 阻塞交互/diagnostic 直打前让位：擦除动态区，交互卡打在原位；重复调用无副作用
        self._paused = True
        self._erase()

    def resume(self) -> None:
        self._paused = False
        # 未激活区间不重绘（验收反馈 R5）：close 后的间隙段（如 pipeline 的
        # ask 卡 pause/resume）若重绘会泄漏上一段的旧计数与正文尾残留
        if self.was_active:
            self._redraw()

    def close(self, usage: dict[str, Any]) -> None:
        """轮末收缩：擦除动态区，原地留一行摘要（💭 …）；未激活轮（降级/管道）不打印。

        收缩后复位全部内部状态（验收反馈 R5）：间隙段的 pause/resume/事件
        不再重绘旧块（此前 was_active 残留 + _text_buf 不清 → 旧计数与正文尾泄漏）。
        """
        if not self.was_active:
            return
        self._erase()
        self._paused = False
        console.print(
            f"[glaucous.dim]💭 思考过程（{self.count} 步{_usage_token_brief(usage)}）— /expand 查看[/]"
        )
        self.count = 0
        self._lines.clear()
        self._text_buf = ""
        self.was_active = False
