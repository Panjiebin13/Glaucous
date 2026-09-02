"""终端交互原语（自 cli.py 拆出，v1.1 评审重构）。

- sanitize_input：cp936 终端孤立代理字符净化（TODO 1.8 崩溃修复路径）；
- select_with_arrows / _default_read_key：原始按键读取的箭头选项选择器（R6）；
- prompt_plan_decision：方案全文 + 二选一决策（v1.1-M1，FR-38）。
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from ..tools.planning import (
    CHOICE_APPROVE,
    CHOICE_FEEDBACK,
    PlanDecision,
)
from ..theme import Markdown, console, escape, make_card


def sanitize_input(raw: str) -> str:
    """净化输入中的孤立代理字符（TODO 1.8：surrogates not allowed 崩溃修复）。

    cp936 终端下 stdin 以 surrogateescape 解码，非法 UTF-8 字节（如中文/全角
    标点的首字节 0xEF）变成 \\udcXX 孤立代理，后续发往 LLM API / 写会话
    JSONL 时 UTF-8 编码必然崩溃。处理：无代理字符原样返回；有则还原原始
    字节，按 UTF-8 → GBK（cp936 终端二次解码）→ replace 的顺序降级，
    保证返回值永远可被 UTF-8 编码。
    """
    try:
        raw.encode("utf-8")
        return raw
    except UnicodeEncodeError:
        data = raw.encode("utf-8", "surrogateescape")
        for encoding in ("utf-8", "gbk"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")


def _arrow_mode() -> bool:
    """箭头选择的运行前提（R6 触发条件）：stdout TTY 且非 plain 降级；

    选项数条件由调用方判断。管道/纯文本模式一律走数字输入回退。"""
    return sys.stdout.isatty() and os.environ.get("GLAUCOUS_INPUT", "").strip().lower() != "plain"


def select_with_arrows(question: str, options: list[str],
                       read_key: Callable[[], str] | None = None) -> int | None:
    """箭头键选项选择器（v1.1 打磨 R6，对齐 Claude Code 交互）。

    返回选中项索引；Esc / Ctrl+C / 任何异常返回 None，由调用方走数字回退或取消语义。
    实现选型（B2）：三处回调是运行中 asyncio 循环内的同步函数，prompt_toolkit
    Application 无法同步 run，故用终端原始按键读取（与事件循环无关）：
    Windows msvcrt.getwch，POSIX termios/tty 临时 raw（try/finally 还原）。
    键语义：↑（含 k）/↓（含 j）循环移动，Enter 确认，Esc（\\x1b 后非 [A/[B 即取消）。
    渲染（r6 重绘修复）：每次按键整块重绘（问题 + 选项 + 提示行），重绘前光标
    回块首并 \\x1b[J 清除旧块（容忍行数漂移），当前项 ❯ 高亮；选项/问题按显示
    宽度截为单行（CJK 占 2 格），防终端自动折行再次引入漂移。
    可测性：按键源可注入（read_key），返回语义键 up/down/enter/esc 或单字符。
    """
    if read_key is None:
        read_key = _default_read_key
    index = 0
    n = len(options)

    def _one_line(text: str, max_width: int) -> str:
        """按显示宽度截为单行（chop_cells 按 CJK 占 2 格计量；len() 会低估宽度）。"""
        from rich.cells import chop_cells

        cells = chop_cells(text, max(1, max_width - 1))
        if not cells:
            return ""
        return cells[0] + ("…" if len(cells) > 1 else "")

    def draw(first: bool) -> None:
        nonlocal index
        # 整块重绘（r6 修复）：块 = 问题行 + n 个选项行 + 提示行。原实现漏计
        # 提示行，重绘起点每次低一行，旧块被挤下去残影逐次叠加（WSL 实测复现）。
        block_lines = n + 2
        if not first:
            # 光标回块首并 \x1b[J 清除旧块到底部；原始转义直写底层文件并 flush——
            # 经 rich print 会按可打印宽度计量，纯转义串可能被误换行
            console.file.write(f"\x1b[{block_lines}A\x1b[J")
            console.file.flush()
        width = max(console.width - 4, 20)  # 预留 2 格缩进 + ❯ 前缀，防自动折行
        console.print(f"  [glaucous.sub]{escape(_one_line(question, width))}[/]")
        for i, option in enumerate(options):
            text = _one_line(option, width)
            if i == index:
                console.print(f"  [glaucous.title][bold]❯ {escape(text)}[/bold][/]")
            else:
                console.print(f"    [glaucous.text]{escape(text)}[/]")
        console.print("  [glaucous.muted]↑↓ 选择 · Enter 确认 · Esc 取消[/]")

    try:
        draw(first=True)
        while True:
            key = read_key()
            if key == "enter":
                console.print()
                return index
            if key == "esc":
                console.print()
                return None
            if key == "up":
                index = (index - 1) % n
            elif key == "down":
                index = (index + 1) % n
            elif key == "k":
                index = (index - 1) % n
            elif key == "j":
                index = (index + 1) % n
            draw(first=False)
    except (Exception, KeyboardInterrupt):
        # 契约：任何异常（含 KeyboardInterrupt，非 Exception 子类须显式列出）回退数字输入；
        # 异常前先换行，避免选项块与后续输出粘连（print 失败也不阻断回退）
        try:
            console.print()
        except Exception:  # noqa: BLE001
            pass
        return None


def _default_read_key() -> str:
    """平台默认按键源：返回语义键 up/down/enter/esc 或普通单字符。"""
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\xe0", "\x00"):
            # 功能键为两字符序列：前缀后紧跟方向码，在此消耗并转为语义键；
            # 非方向键的功能键返回空串（主循环忽略）
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(ch2, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x1b":
            return "esc"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch

    import select as _select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def read_one() -> str:
        # 直接 os.read（r6 按键修复）：sys.stdin 缓冲层可能一次吞掉整个转义序列
        # （如 ↑ 的 \x1b[A 三字节），select 探测 fd 时会误报「无后续字节」，
        # 方向键被误判为 Esc 而取消（WSL 实测复现）；os.read 与 select 同源不冲突
        return os.read(fd, 1).decode("utf-8", errors="replace")

    def esc_followup() -> str | None:
        # \x1b 后续有字节且为 [A/[B（或应用光标模式 OA/OB）→ 方向键；否则视为单独 Esc
        ready, _, _ = _select.select([fd], [], [], 0.1)
        return read_one() if ready else None

    try:
        tty.setraw(fd)
        ch = read_one()
        if ch == "\x1b":
            nxt = esc_followup()
            if nxt in ("[", "O"):
                code = read_one()
                if code == "A":
                    return "up"
                if code == "B":
                    return "down"
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt_plan_decision(plan: str) -> PlanDecision:
    """打印方案全文并读取二选一决策（v1.1-M1，FR-38）；非法输入重问；

    Ctrl+C/EOF 视为②修改意见（模型据此停下/修订，不视为批准，spec §4.3）。"""
    # 方案卡：框内标题栏 + Markdown 正文（markdown.* 走主题色板；
    # rich Markdown 不解析 console markup，方括号天然安全，无需 escape）
    console.print()
    table = make_card(":clipboard: 方案已就绪")
    if plan.strip():
        table.add_row(Markdown(plan))
    console.print(table)

    # 选项：语义色区分（1=海草绿批准 / 2=晚霞橙修改意见，可附反馈）
    console.print("  [glaucous.ok][bold]1️⃣  批准执行[/][/]")
    console.print("  [glaucous.warn][bold]2️⃣  提出修改意见[/][/]")

    while True:
        try:
            raw = sanitize_input(console.input("  [glaucous.sub]请选择 :computer_mouse:（2️⃣ 可附加反馈，格式：2 反馈内容）: [/]")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()  # 换行
            return PlanDecision(choice=CHOICE_FEEDBACK, feedback=None)
        if not raw:
            continue
        choice, _, feedback = raw.partition(" ")
        if choice == "1":
            return PlanDecision(choice=CHOICE_APPROVE)
        if choice == "2":
            return PlanDecision(choice=CHOICE_FEEDBACK, feedback=feedback.strip() or None)
        # 错误提示：陶土红 + :x:
        console.print("  [glaucous.error][bold]  :x: 无效选择，请输入 1 或 2。[/][/]")

