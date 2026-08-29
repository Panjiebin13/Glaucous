"""CLI REPL：M3-UI 主题渲染 + 输入层 + m3-day5 体验与扩展功能（合并版）。

UI 层（M3-UI 分支）：
- rich 主题渲染单一出口（theme.py：Console/色板/卡片/文档渲染/ctx 圆环），
  动态内容一律 escape 防 markup 注入；流式正文 markup/emoji 关闭保真；
- 输入层 prompt_toolkit（PT_STYLE 语义色 + ↑↓ 历史 + Ctrl+R 搜索 +
  斜杠命令补全），非 TTY 回退 console.input（TODO 1.8 cp936 净化路径不变）；
- 三张交互卡（审批/方案/提问）rich Table 化 + /view 文件渲染 + 压缩意象。

功能层（m3-day5 分支）：
- ReplContext 聚合全部可重建组件；斜杠命令全集经 commands.handle_command
  本地分派（绝不发给 LLM）；/clear、/resume 整体替换后经 rebuild_loop 重建
  （回调经 ctx 间接引用，不捕获旧对象，Day5 Plan D8）；
- 模型注册表（models.toml）：启动取默认档案，/model 切换 + 连通性校验；
- 技能索引注入 system prompt + load_skill 惰性加载通道（LoadSkillTool）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .agent.loop import AgentLoop
from .agent.state import POLICY_AUTO_APPROVE, POLICY_PER_ACTION, SessionState
from .commands import COMMAND_META, ReplContext, handle_command, reset_turn_buffers
from .config import ConfigError, load_config
from .context.budget import BudgetReport, build_report
from .context.history import History
from .extensions.memory import MemoryStore
from .extensions.rules import load_rules
from .extensions.skills import SkillRegistry
from .llm.client import LLMClient
from .llm.registry import RegistryError, load_registry
from .permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from .permission.risk import Risk
from .permission.workspace import Workspace, WorkspaceEscape
from .tools.base import ToolRegistry
from .tools.files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from .tools.interactive import AskUserTool
from .tools.memory_tool import MemorySaveTool
from .tools.output import ReadOutputTool
from .tools.planning import (
    CHOICE_BUILD_AUTO_APPROVE,
    CHOICE_BUILD_PER_ACTION,
    CHOICE_KEEP_PLANNING,
    PlanDecision,
    ReadPlanTool,
    SubmitPlanTool,
)
from .tools.search import GrepTool
from .tools.shell import BashTool
from .tools.skill_tool import LoadSkillTool
from .ui.prompts import build_system_prompt

# 输入层（M3 3.3）：主输入 prompt_toolkit（↑↓ 历史/Ctrl+R 搜索/语义样式/斜杠补全），
# 渲染仍走 rich；非交互（管道/重定向）回退 console.input，保住 TODO 1.8 的
# cp936 stdin 净化路径。prompt_toolkit 相关导入全部延迟到使用点
# （make_prompt_session / repl 内），依赖损坏不拒启动（m3-day5 plan §4.3 降级②）；
# 提示符类名与 theme.PT_STYLE 语义名一一对应。
# rich 渲染：Console/色板单一出口（theme.py），动态内容一律 escape 防 markup 注入；
# 思考折叠动态区用 rich.live.Live（v1.1 打磨 R3）
from rich.console import Group
from rich.live import Live
from rich.markup import escape
from .theme import (
    Markdown,
    PT_STYLE,
    console,
    ctx_ring,
    make_card,
    render_answer_card,
    render_code_doc,
    render_csv_doc,
    render_markdown_doc,
    render_text_doc,
)

def render_banner(model_name: str, mode: str) -> None:
    """启动 Banner（主题设计 §2.1）：卡片化呈现，附加项 A 追加模型/模式行。

    make_card 的框内标题栏即 §2.1 mockup 的 ┌─ 标题 ─┐ 形态；副标语走
    glaucous.sub（海盐青斜体），操作提示走 glaucous.muted（晴空灰）。
    Banner 为启动快照：/model 切换后不刷新（数据源 ctx.current_model，S8 口径）。
    """
    table = make_card(":cloud: Glaucous · coding agent（M3 体验与扩展）")
    table.add_row("[glaucous.sub]雨过天青，海鸥滑翔，代码自有清凉[/]")
    table.add_row("[glaucous.muted]输入任务开始对话，/help 查看命令，/exit 退出。Plan 只读探索，Build 写操作走审批。[/]")
    table.add_row(f"[glaucous.muted]当前模型 {escape(model_name)} · 模式 {escape(mode)}[/]")
    console.print(table)


# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要，M3 折叠升级）
RESULT_TAIL_LINES = 3

# markdown 文档卡片渲染的行数上限：read_file 打开 .md 时，内容行数 ≤ 此值才
# 渲染卡片（防长文档刷屏）；超长维持默认摘要并提示 /view 主动查看
MD_RENDER_MAX_LINES = 200

# resume 时回放的最近消息条数（仅 UI 摘要，History 本身全量加载）
RESUME_PREVIEW_MESSAGES = 6

# prompt_toolkit 补全的斜杠命令全集（16 个：既有 14 + /view、/expand，v1.1 R2）
SLASH_COMMANDS = [
    "/help", "/plan", "/build", "/compact", "/clear", "/resume", "/model",
    "/memory", "/rules", "/skills", "/init", "/stop", "/exit", "/quit",
    "/view", "/expand",
]

# 需文件路径参数的命令（补全器据此在工作区内补全路径，v1.1 R2）
PATH_ARG_COMMANDS = {"/view"}

# 思考区动态区高度上限（行），滚动显示最近事件（v1.1 R3）
THINKING_MAX_LINES = 8


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


class ThemeRenderer:
    """commands.py 斜杠命令的渲染接口适配（M3-UI theme.py 单一出口）。

    commands 只依赖 note/info/error/console/model_name/last_budget/
    render_budget_report/retry 八个成员（鸭子类型），全部以 M3-UI 主题
    色板实现：动态内容 escape 防 markup 注入，语义色与主循环一致。
    """

    console = console  # 复用 theme.py 的主题 Console（单一出口）

    def __init__(self) -> None:
        self.model_name = ""       # /model 切换后更新（提示符头部动态跟随）
        self.last_budget = None    # /clear、/resume 后由命令层置 None
        # 思考区 Live 钩子（v1.1 R3）：默认 no-op；repl 启动时按折叠模式注入真实实现，
        # 使 retry 通知在阻塞展示前暂停动态区（不捕获 ctx，不产生 cli↔commands 环）
        self._live_hooks: dict[str, Any] = {"pause": lambda: None, "resume": lambda: None}

    def note(self, text: str) -> None:
        """中性信息（晴空灰）：列表、路径、提示类输出。"""
        console.print(f"[glaucous.muted]  {escape(str(text))}[/]")

    def info(self, text: str) -> None:
        """成功/状态变更（天青）：模式切换、写入完成等。"""
        console.print(f"[glaucous.title]  ◆ {escape(str(text))}[/]")

    def error(self, text: str) -> None:
        """错误（陶土红）。"""
        console.print(f"[glaucous.error]  ✘ {escape(str(text))}[/]")

    def retry(self, attempt: int, delay: float) -> None:
        """LLM 退避重试通知（晚霞橙，Day5 Plan §4.2 on_retry 钩子）。

        v1.1 R3：阻塞展示点——进入前暂停思考区 Live、返回后恢复（try/finally）。
        """
        pause = self._live_hooks["pause"]
        resume = self._live_hooks["resume"]
        pause()
        try:
            console.print(f"[glaucous.warn]  ↻ 重试中（第 {attempt} 次，预计等待 {delay:.0f}s）[/]")
        finally:
            resume()

    def render_budget_report(self, report: BudgetReport, mode: str | None = None,
                             policy: str | None = None) -> None:
        """/compact 后的占用报告：ctx 圆环三档变色 + token 用量 + 模式附注。"""
        ring, level_style = ctx_ring(report.percent)
        note = f" · {mode}" if mode else ""
        if policy:
            note += "·每次审批" if policy == POLICY_PER_ACTION else "·auto"
        console.print(
            f"  [{level_style}]{ring}[/] "
            f"[glaucous.muted]{report.used // 1000}k/{report.limit // 1000}k tokens{note}[/]"
        )


def _arrow_mode() -> bool:
    """箭头选择的运行前提（R6 触发条件）：stdout TTY 且非 plain 降级；
    选项数条件由调用方判断。管道/纯文本模式一律走数字输入回退。"""
    return sys.stdout.isatty() and os.environ.get("GLAUCOUS_INPUT", "").strip().lower() != "plain"


def make_ask_callback(ctx: ReplContext):
    """ask_user 终端实现（任务 2.3）：提问卡 + 候选列表 + 序号/自由文本回答。

    EOF/Ctrl+C 返回 None → 工具回喂「用户未响应」控制信号（非交互环境不挂死）。
    v1.1 R6：options 非空且 TTY 非降级 → 箭头选择（选中返回选项原文，取消 None）；
    R3：阻塞交互前后暂停/恢复思考区，并记录 ask 伪事件供 /expand 回看。
    """

    def ask(question: str, options: list[str]) -> str | None:
        ctx.live_hooks["pause"]()
        try:
            console.print()
            table = make_card(":dove: 想请教你")
            # 问题正文走 Markdown（markdown.* 主题色板；方括号天然安全，无需 escape）
            if question.strip():
                table.add_row(Markdown(question))
            for i, option in enumerate(options, 1):
                table.add_row(f"[glaucous.title][{i}] {escape(option)}[/]")
            console.print(table)
            if options and _arrow_mode():
                idx = select_with_arrows("请选择：", options)
                result = options[idx] if idx is not None else None
            else:
                try:
                    raw = sanitize_input(console.input("  [glaucous.sub]回答（输入候选序号或自由文本）: [/]")).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    result = None
                else:
                    result = options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else raw
            ctx.turn_events.append(("ask", {"summary": f"提问「{question[:40]}」→ 回答：{result or '（未响应）'}"}))
            ctx.live_hooks.get("step", lambda: None)()  # N 口径：交互伪事件计入思考步数（§3.1）
            return result
        finally:
            ctx.live_hooks["resume"]()

    return ask


def make_decision_callback(ctx: ReplContext):
    """审批三选项决策回调（per-action 弹三选项；auto-approve 守卫在 gate 内先行处理）。

    破坏性命令（DANGEROUS/区外写）用 ⚠ 警示 + 命令全文（主题色渲染）。
    v1.1 R6：统一三选项箭头选择（DANGEROUS 呈现不分列，安全语义由 gate 守卫兜底，
    r2-S3 决策）；取消（Esc）= 拒绝、理由「用户取消」；附加项 B：拒绝理由输入保护。
    """

    def _reject_reason() -> str | None:
        # 附加项 B：EOF/Ctrl+C 视为理由「用户取消」继续拒绝（不再落入本轮失败兜底）
        try:
            return sanitize_input(console.input("  [glaucous.sub]拒绝理由（可留空）: [/]")).strip() or None
        except (EOFError, KeyboardInterrupt):
            console.print()
            return "用户取消"

    def decide(action: ApprovalAction) -> ApprovalDecision:
        ctx.live_hooks["pause"]()
        try:
            risk_note = {
                Risk.DANGEROUS: " :warning: 破坏性操作（不可批量放行）",
                Risk.WRITE: "",
                Risk.SAFE: "",
            }.get(action.risk, "")
            console.print()
            table = make_card(key_value=True)
            table.add_row(
                "需要确认",
                f"[glaucous.text][bold]{escape(str(action.kind))} {escape(str(action.target))}[/][/]",
            )
            if risk_note:
                table.add_row("风险", f"[glaucous.warn]{risk_note}[/]")
            console.print(table)
            if action.detail:
                # diff/说明可能多行，只展示前 60 行
                detail_lines = action.detail.splitlines()
                for line in detail_lines[:60]:
                    console.print(f"[glaucous.sub]    {escape(line)}[/]")
                if len(detail_lines) > 60:
                    console.print(f"[glaucous.muted]    …（详情共 {len(detail_lines)} 行，已截断展示）[/]")
            dangerous = action.risk == Risk.DANGEROUS
            decision: ApprovalDecision | None = None
            if _arrow_mode():
                # 三选项对齐 ApprovalDecision.choice（概设 §5.3、FR-11）
                idx = select_with_arrows("请选择：", ["同意", "同意同类型", "拒绝"])
                if idx is None:
                    decision = ApprovalDecision(choice="reject", reason="用户取消")
                elif idx == 0:
                    decision = ApprovalDecision(choice="approve")
                elif idx == 1:
                    decision = ApprovalDecision(choice="approve_type")
                else:
                    decision = ApprovalDecision(choice="reject", reason=_reject_reason())
            else:
                while decision is None:
                    try:
                        if dangerous:
                            raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[c] 拒绝(附理由): [/]")).strip()
                        else:
                            raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[b] 同意同类型  \\[c] 拒绝(附理由): [/]")).strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print()
                        decision = ApprovalDecision(choice="reject", reason="用户中断审批")
                        break
                    if raw in ("a", "A", "y", "Y"):
                        decision = ApprovalDecision(choice="approve")
                    elif not dangerous and raw in ("b", "B"):
                        decision = ApprovalDecision(choice="approve_type")
                    elif raw in ("c", "C", "n", "N"):
                        decision = ApprovalDecision(choice="reject", reason=_reject_reason())
                    else:
                        console.print("[glaucous.error]  无效输入，请重试。[/]")
            ctx.turn_events.append(("decision", {
                "summary": f"审批 {action.kind} {action.target} → {decision.choice}",
            }))
            ctx.live_hooks.get("step", lambda: None)()  # N 口径：交互伪事件计入思考步数（§3.1）
            return decision
        finally:
            ctx.live_hooks["resume"]()

    return decide


def prompt_plan_decision(plan: str) -> PlanDecision:
    """打印方案全文并读取三选一决策；非法输入重问；Ctrl+C 视为③继续讨论。"""
    # 方案卡：框内标题栏 + Markdown 正文（markdown.* 走主题色板；
    # rich Markdown 不解析 console markup，方括号天然安全，无需 escape）
    console.print()
    table = make_card(":clipboard: 方案已就绪")
    if plan.strip():
        table.add_row(Markdown(plan))
    console.print(table)

    # 选项：语义色区分（1=海草绿常规 / 2=晚霞橙需注意 / 3=天青继续讨论）
    console.print("  [glaucous.ok][bold]1️⃣  开始构建，每次请求权限[/][/]")
    console.print("  [glaucous.warn][bold]2️⃣  开始构建，同意所有权限[/][/]")
    console.print("  [glaucous.title][bold]3️⃣  继续讨论一下[/][/]")

    while True:
        try:
            raw = sanitize_input(console.input("  [glaucous.sub]请选择 :computer_mouse:（3️⃣ 可附加反馈，格式：3 反馈内容）: [/]")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()  # 换行
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=None)
        if not raw:
            continue
        choice, _, feedback = raw.partition(" ")
        if choice == CHOICE_KEEP_PLANNING:
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=feedback.strip() or None)
        if choice in (CHOICE_BUILD_PER_ACTION, CHOICE_BUILD_AUTO_APPROVE):
            return PlanDecision(choice=choice, feedback=feedback.strip() or None)
        # 错误提示：陶土红 + :x:
        console.print("  [glaucous.error][bold]  :x: 无效选择，请输入 1、2 或 3。[/][/]")


def select_with_arrows(question: str, options: list[str],
                       read_key: Callable[[], str] | None = None) -> int | None:
    """箭头键选项选择器（v1.1 打磨 R6，对齐 Claude Code 交互）。

    返回选中项索引；Esc / Ctrl+C / 任何异常返回 None，由调用方走数字回退或取消语义。
    实现选型（B2）：三处回调是运行中 asyncio 循环内的同步函数，prompt_toolkit
    Application 无法同步 run，故用终端原始按键读取（与事件循环无关）：
    Windows msvcrt.getwch，POSIX termios/tty 临时 raw（try/finally 还原）。
    键语义：↑（含 k）/↓（含 j）循环移动，Enter 确认，Esc（\x1b 后非 [A/[B 即取消）。
    渲染：ANSI 光标上移重绘选项块，当前项 ❯ 高亮；全部经 theme.console。
    可测性：按键源可注入（read_key），返回语义键 up/down/enter/esc 或单字符。
    """
    if read_key is None:
        read_key = _default_read_key
    index = 0
    n = len(options)

    def draw(first: bool) -> None:
        nonlocal index
        if not first:
            # 光标上移重绘：提示行 + n 个选项行（选项单行截断，避免折行错位）；
            # 原始 ANSI 转义不经 markup 解析（markup=False）
            console.print(f"\x1b[{n + 1}A", end="", markup=False)
        width = max(console.width - 6, 20)
        console.print(f"  [glaucous.sub]{escape(question)}[/]")
        for i, option in enumerate(options):
            text = option if len(option) <= width else option[: width - 1] + "…"
            if i == index:
                console.print(f"  [glaucous.title][bold]❯ {escape(text)}[/bold][/]")
            else:
                console.print(f"    [glaucous.text]{escape(text)}[/]")

    try:
        draw(first=True)
        console.print("  [glaucous.muted]↑↓ 选择 · Enter 确认 · Esc 取消[/]")
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
        return sys.stdin.read(1)

    def esc_followup() -> str | None:
        # \x1b 后续有字符且为 [A/[B → 方向键；否则视为单独 Esc（取消）
        ready, _, _ = _select.select([sys.stdin], [], [], 0.05)
        return read_one() if ready else None

    try:
        tty.setraw(fd)
        ch = read_one()
        if ch == "\x1b":
            nxt = esc_followup()
            if nxt == "[":
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


def render_event(event: str, payload: dict[str, Any], state: SessionState) -> None:
    """loop 事件 → 主题化渲染（⏺ 动作行 / ⎿ 结果行，学 Claude Code 的密度）。"""
    if event == "text":
        # 流式正文：markup/emoji 关闭保证逐字保真（模型输出里的 [...] 不被吞）
        console.print(payload["text"], end="", soft_wrap=True, markup=False, emoji=False)
    elif event == "diagnostic":
        # 终止诊断（步数上限/解析熔断）：loop 显式通知，保证多步轮中必达
        console.print(f"[glaucous.warn]\n  ⎿ {escape(payload['text'])}[/]")
    elif event == "mode_changed":
        # 模式切换/回归：提示符由 REPL 每轮按 state 重算，这里给一行可读反馈
        policy_note = (
            "·每次审批" if payload["policy"] == POLICY_PER_ACTION else "·自动放行"
        )
        console.print(f"[glaucous.title]  ◆ {escape(payload['reason'])}（{payload['mode']}{policy_note}）[/]")
    elif event == "compressed":
        # 压缩意象（主题设计 §4）：🌊 潮汐——涨潮了，压缩上下文
        if payload["stage"] == "L1":
            text, style = "🌊 涨潮了，归档早期对话", "glaucous.sub"
        elif payload.get("ok"):
            text, style = "🌊 涨潮了，压缩上下文", "glaucous.title"
        else:
            text, style = "🌊 潮水不退，继续精简对话", "glaucous.warn"
        console.print(f"[{style}]  {text}[/]")
    elif event == "tool_start":
        call = payload["call"]
        brief = call.arguments if len(call.arguments) <= 80 else call.arguments[:80] + "…"
        console.print(f"\n  ⏺ [glaucous.tool]{escape(call.name)}[/] [glaucous.text]{escape(brief)}[/]")
    elif event == "tool_end":
        result = payload["result"]
        lines = result.content.splitlines()
        if result.ok:
            if len(lines) <= RESULT_TAIL_LINES:
                summary = " | ".join(lines) if lines else "（无输出）"
            else:
                summary = f"…共 {len(lines)} 行 | " + " | ".join(lines[-RESULT_TAIL_LINES:])
        else:
            summary = f"✘ {result.content}"
        # 成功海草绿 / 失败陶土红（主题设计 §2.3）
        level_style = "glaucous.ok" if result.ok else "glaucous.error"
        console.print(f"[{level_style}]    ⎿ {escape(summary)}[/]")


def _tool_brief(arguments: str) -> str:
    """工具参数摘要：≤80 字原样，超长截断（实时行与思考区单行摘要共用）。"""
    return arguments if len(arguments) <= 80 else arguments[:80] + "…"


def _thinking_line(event: str, payload: dict[str, Any]) -> str:
    """非 text 事件 → 思考区单行摘要（纯文本，v1.1 R3；text 增量不进思考区）。

    与 render_event 同一信息源的紧凑形态；/expand 回看时用 render_event 完整重放。
    """
    if event == "diagnostic":
        return f"⎿ {payload.get('text', '')}"
    if event == "mode_changed":
        policy = "·每次审批" if payload.get("policy") == POLICY_PER_ACTION else "·自动放行"
        return f"◆ {payload.get('reason', '')}（{payload.get('mode', '')}{policy}）"
    if event == "compressed":
        if payload.get("stage") == "L1":
            return "🌊 涨潮了，归档早期对话"
        return "🌊 涨潮了，压缩上下文" if payload.get("ok") else "🌊 潮水不退，继续精简对话"
    if event == "tool_start":
        call = payload["call"]
        return f"⏺ {call.name} {_tool_brief(call.arguments)}"
    if event == "tool_end":
        result = payload["result"]
        lines = (result.content or "").splitlines()
        if result.ok:
            summary = " | ".join(lines[-RESULT_TAIL_LINES:]) if lines else "（无输出）"
            if len(lines) > RESULT_TAIL_LINES:
                summary = f"…共 {len(lines)} 行 | {summary}"
        else:
            summary = f"✘ {result.content}"
        return f"⎿ {summary}"
    return event


class ThinkingView:
    """思考过程折叠动态区（v1.1 打磨 R3）：rich.live.Live 单行计数 + 最近事件滚动。

    时序契约（§3.1）：任务轮开始时 start；非 text 事件经 add 收纳；轮末 close 原地
    收缩为摘要行（💭 思考过程（N 步 · ↑Xk ↓Yk tokens）— /expand 查看）。启动失败降级：
    本轮改实时逐条打印，不再尝试（降级后 add 内部直接打印摘要行）。
    pause/resume 供阻塞交互（四阻塞点）暂停/恢复动态区；缓冲记录在 on_event 层，
    与 Live 是否存活无关（管道下 /expand 仍可用）。
    """

    def __init__(self) -> None:
        self.count = 0
        self._live: Live | None = None
        self._lines: list[str] = []
        self._degraded = False

    @property
    def active(self) -> bool:
        return self._live is not None

    def start(self) -> None:
        if self._degraded:
            return
        if self._live is None:
            try:
                self._live = Live(console=console, refresh_per_second=8, transient=False)
                self._live.start()
                self._live.update(self._renderable())
            except Exception:  # noqa: BLE001 —— 终端不支持 Live：降级实时打印，本轮不再尝试
                self._live = None
                self._degraded = True

    def add(self, event: str, payload: dict[str, Any]) -> None:
        self.count += 1
        line = _thinking_line(event, payload)
        if not self.active:  # 未启动或已降级：实时打印摘要行（降级路径）
            console.print(f"[glaucous.dim]  {escape(line)}[/]")
            return
        self._lines.append(line)
        self._live.update(self._renderable())

    def note_step(self) -> None:
        """交互伪事件计数（不占动态区行）：交互以卡片形式呈现，但 N 口径需含（§3.1：
        N = 非 text 事件 + 交互伪事件，与缓冲//expand 同一口径）。经 live_hooks["step"] 接线。"""
        self.count += 1

    def _renderable(self) -> Group:
        header = f"[glaucous.sub]⚙ 思考中 · {self.count} 步[/]"
        recent = [f"[glaucous.dim]{escape(line)}[/]" for line in self._lines[-THINKING_MAX_LINES:]]
        return Group(*([header] + recent))

    def pause(self) -> None:
        # 阻塞交互前让位：保留已渲染内容，交互输出正常打印；重复调用无副作用（契约）
        if self.active:
            self._live.stop()

    def resume(self) -> None:
        if self._live is not None and not self._degraded:
            try:
                self._live.start()
                self._live.update(self._renderable())
            except Exception:  # noqa: BLE001 —— 恢复失败同样降级实时打印，不阻断会话
                self._live = None
                self._degraded = True

    def close(self, usage: dict[str, Any]) -> None:
        """轮末收缩：原地更新为摘要行后停止（transient=False 保留收缩结果）。"""
        if not self.active:
            return
        summary = f"[glaucous.dim]💭 思考过程（{self.count} 步{_usage_token_brief(usage)}）— /expand 查看[/]"
        self._live.update(summary)
        self._live.stop()
        self._live = None


def _render_md_tool_end(payload: dict[str, Any], ws: Workspace) -> bool:
    """agent 路径：read_file 打开 markdown 时渲染卡片替代默认摘要（尽力而为）。

    判定：tool_end 事件、工具为 read_file、结果 ok、path 以 .md/.markdown 结尾。
    - 行数 ≤ MD_RENDER_MAX_LINES → 沙箱校验后读**文件原文**渲染卡片
      （read_file 结果带行号 files.py:96，渲染必须读原文才不破坏 md 结构）；
    - 超长 → 打印 /view 提示并返回 False（维持默认 3 行摘要）；
    - 渲染失败（越界/IO/非 UTF-8）→ 返回 False 回退默认摘要，不阻断会话。
    """
    call = payload.get("call")
    result = payload.get("result")
    if call is None or getattr(call, "name", "") != "read_file":
        return False
    if result is None or not result.ok:
        return False
    try:
        args = json.loads(getattr(call, "arguments", "") or "{}")
    except json.JSONDecodeError:
        return False
    path = args.get("path")
    if not isinstance(path, str) or not path.lower().endswith((".md", ".markdown")):
        return False
    lines = (result.content or "").count("\n") + 1
    if lines > MD_RENDER_MAX_LINES:
        console.print(f"[glaucous.muted]  （markdown 较长，可用 /view {escape(path)} 查看渲染）[/]")
        return False
    try:
        target = ws.check(path)
        text = target.read_text(encoding="utf-8")
    except (WorkspaceEscape, OSError, UnicodeDecodeError):
        return False
    try:
        rel = target.relative_to(ws.root)
    except ValueError:
        rel = target
    render_markdown_doc(f":book: {rel}", text)
    return True


# /view 按后缀分发的渲染器注册表（M3 3.3 扩展：代码/文本/CSV）
# 新增类型只需在此加一行，不碰 _cmd_view 主逻辑；未知类型回退提示走 read_file
_VIEW_RENDERERS: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".py": "code",
    ".pyi": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".go": "code",
    ".java": "code",
    ".rs": "code",
    ".c": "code",
    ".h": "code",
    ".cpp": "code",
    ".sh": "code",
    ".bash": "code",
    ".toml": "code",
    ".yaml": "code",
    ".yml": "code",
    ".json": "code",
    ".html": "code",
    ".css": "code",
    ".sql": "code",
    ".txt": "text",
    ".log": "text",
    ".csv": "csv",
    ".tsv": "csv",
}


def _detect_binary(data: bytes) -> bool:
    """NUL 字节检测二进制（比后缀判断可靠，防伪装后缀）。"""
    return b"\x00" in data[:8192]


def _cmd_view(path_arg: str, ws: Workspace) -> None:
    """/view <路径>：按类型渲染查看工作区内文件（沙箱校验，只读，Plan/Build 均可用）。

    类型分发：md→Markdown 卡片 / 代码→Syntax 语法高亮 / 文本→卡片原文 /
    csv→表格分列 / 未知或二进制→提示走 read_file。行数 > MD_RENDER_MAX_LINES
    时打印提示并维持摘要，不整屏刷出。
    """
    if not path_arg:
        console.print("[glaucous.warn]  /view <路径>：以渲染形式查看工作区内的文件（md/代码/文本/csv）。[/]")
        return
    try:
        target = ws.check(path_arg)
    except WorkspaceEscape as exc:
        console.print(f"[glaucous.error]  ✘ {escape(str(exc))}[/]")
        return
    if not target.exists() or not target.is_file():
        console.print(f"[glaucous.error]  ✘ 文件不存在或不是文件: {escape(str(target))}[/]")
        return
    try:
        rel = target.relative_to(ws.root)
    except ValueError:
        rel = target
    title = f":book: {rel}"

    kind = _VIEW_RENDERERS.get(target.suffix.lower())
    if kind is None:
        console.print(
            f"[glaucous.warn]  {escape(target.name)} 暂不支持渲染（{escape(target.suffix or '无后缀')}），"
            "建议用 read_file 查看原文。[/]"
        )
        return

    try:
        data = target.read_bytes()
    except OSError as exc:
        console.print(f"[glaucous.error]  ✘ 读取失败: {escape(str(exc))}[/]")
        return
    if _detect_binary(data):
        console.print(f"[glaucous.warn]  {escape(target.name)} 是二进制文件，建议用 read_file 查看原文。[/]")
        return

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        console.print(f"[glaucous.error]  ✘ 非 UTF-8 文本，无法渲染: {escape(str(exc))}[/]")
        return
    if text.count("\n") + 1 > MD_RENDER_MAX_LINES:
        console.print(f"[glaucous.muted]  （{escape(target.name)} 较长 {text.count(chr(10)) + 1} 行，未整屏渲染）[/]")
        return

    if kind == "markdown":
        render_markdown_doc(title, text)
    elif kind == "code":
        render_code_doc(title, target, text)
    elif kind == "text":
        render_text_doc(title, text)
    elif kind == "csv":
        render_csv_doc(title, text)


def prompt_mode(state: SessionState) -> str:
    """提示符中的模式段：build 追加审批策略缩写，提醒当前授权语义。"""
    if state.mode == "build":
        policy = "每次审批" if state.approval_policy == POLICY_PER_ACTION else "auto"
        return f"build·{policy}"
    return "plan"


def render_prompt_header(model_name: str, report: BudgetReport) -> None:
    """输入区头部（rich 静态渲染，M3 3.3）：模型 + ctx 占用圆环与 token 用量行。

    模型名右侧依次为 ctx 占用圆环（theme.ctx_ring 三档变色，阈值与 budget
    同源）与 token 用量（48k/128k tokens）；模式段（🌊 plan >）已并入输入行
    前缀，由输入方渲染（tty 为 prompt_toolkit 前缀，管道回退拼纯文本提示符）。
    """
    ring, level_style = ctx_ring(report.percent)
    console.print(
        f"\n[glaucous.muted]{escape(model_name)}[/]  "
        f"[{level_style}]{ring}[/] [glaucous.muted]{report.used // 1000}k/{report.limit // 1000}k tokens[/]"
    )


# ---------------------------------------------------------------------------
# 工具装配与循环重建（m3-day5：ReplContext 驱动，/clear、/resume 共用）
# ---------------------------------------------------------------------------


def build_registry(ctx: ReplContext, ws: Workspace, thinking: ThinkingView | None = None) -> ToolRegistry:
    """装配全量工具：只读四件 + 双写 + submit_plan + 交互/记忆/回取 + load_skill。

    权限管线注入 registry（dispatch 层统一审批）；交互回调经 ctx 注入；
    read_output/read_plan 的目录由系统派生（无沙箱面，Day4 Plan D8）。
    """
    registry = ToolRegistry()
    reader = ReadFileTool(ws)
    registry.register(reader)
    registry.register(ListDirTool(ws, reader=reader))
    registry.register(GrepTool(ws, reader=reader))
    registry.register(BashTool(ws))
    registry.register(WriteFileTool(ws, reader=reader))
    registry.register(EditFileTool(ws, reader=reader))
    registry.set_approval_pipeline(ctx.pipeline)

    # M2 任务 2.2/2.3：记忆写入与用户求助（回调经 ctx：伪事件记录 + Live 钩子，v1.1 R3/R6）
    registry.register(MemorySaveTool(ctx.memory_store))
    registry.register(AskUserTool(ask=make_ask_callback(ctx)))
    # M2 任务 2.5/2.7：L0 落盘回取 + 方案回读
    registry.register(ReadOutputTool(ctx.outputs_dir))
    registry.register(ReadPlanTool(ctx.plans_dir))
    # M3 任务 3.5：技能惰性加载通道（两段式：索引已注入，正文经此取回）
    registry.register(LoadSkillTool(ctx.skills))

    def confirm(plan: str) -> PlanDecision:
        """三选一交互：卡片呈现 + 状态切换接线（经 ctx.state，D8）。

        v1.1 R6：TTY 非降级时箭头选择（取消 = 选三，feedback 落「用户取消」，
        r2-S4：PlanDecision 无 reason 字段）；第三项文案对齐 FR-08 字面；
        R3：阻塞交互前后暂停/恢复思考区，并记录 plan_decision 伪事件。
        """
        ctx.live_hooks["pause"]()
        try:
            decision: PlanDecision | None = None
            if _arrow_mode():
                idx = select_with_arrows(
                    "请选择：", ["执行（逐次审批）", "执行（自动批准）", "继续讨论一下"]
                )
                if idx == 0:
                    decision = PlanDecision(choice=CHOICE_BUILD_PER_ACTION, feedback=None)
                elif idx == 1:
                    decision = PlanDecision(choice=CHOICE_BUILD_AUTO_APPROVE, feedback=None)
                else:  # 选三或取消（Esc）：继续讨论，取消意图统一落 feedback
                    decision = PlanDecision(
                        choice=CHOICE_KEEP_PLANNING,
                        feedback="用户取消" if idx is None else None,
                    )
            else:
                decision = prompt_plan_decision(plan)
            if decision.choice == CHOICE_BUILD_PER_ACTION:
                ctx.state.enter_build(POLICY_PER_ACTION)
            elif decision.choice == CHOICE_BUILD_AUTO_APPROVE:
                ctx.state.enter_build(POLICY_AUTO_APPROVE)
            ctx.turn_events.append(("plan_decision", {
                "summary": f"方案决策 → {decision.choice}"
                + (f"（反馈：{decision.feedback}）" if decision.feedback else ""),
            }))
            ctx.live_hooks.get("step", lambda: None)()  # N 口径：交互伪事件计入思考步数（§3.1）
            return decision
        finally:
            ctx.live_hooks["resume"]()

    registry.register(SubmitPlanTool(confirm=confirm, plans_dir=ctx.plans_dir))
    return registry


def make_on_event(ctx: ReplContext, ws: Workspace, thinking: ThinkingView | None = None):
    """loop 事件回调：主题化渲染 + 思考折叠分流 + budget 缓存（状态栏数据源）。

    v1.1 R3 分流：text 增量两种模式下均逐字实时打印（不进思考区）；非 text 事件
    一律先记入 turn_events 缓冲（/expand 口径）——折叠开且 Live 存活时渲染进动态区，
    否则维持现状逐条实时打印。tool_end 的 markdown 卡片为直接打印，前后暂停/恢复 Live。
    """

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            ctx.stream_state["printed"] = True
            render_event(event, payload, ctx.state)
            return
        if event == "budget":
            ctx.last_budget = payload
        # 非 text 事件统一缓冲（含降级模式）：① 类条目，与思考区/摘要行同一口径（r4-B1）
        ctx.turn_events.append((event, payload))
        if thinking is not None and thinking.active:
            if event == "tool_end" and _render_md_tool_end(payload, ws):
                thinking.pause()
                try:
                    thinking.add(event, payload)  # 卡片已直接打印，动态区仍收摘要行保持计数一致
                finally:
                    thinking.resume()
            else:
                thinking.add(event, payload)
            return
        if event == "tool_end" and _render_md_tool_end(payload, ws):
            return
        render_event(event, payload, ctx.state)

    return on_event


def rebuild_loop(ctx: ReplContext, thinking: ThinkingView | None = None) -> None:
    """重建管线与主循环（启动装配与 /clear、/resume 共用入口）。

    state 可能已被整体替换（/clear 重置、/resume 恢复）：管线随新 state
    重建，回调经 ctx 间接引用自动跟随（闭包不捕获旧对象，D8）；
    重建后旧 loop 对象不再被任何入口持有。thinking 为思考区动态区（折叠关闭时为 None）。
    """
    ws = Workspace(ctx.workspace, read_only_extra=ctx.config.read_only_extra)
    ctx.pipeline = ApprovalPipeline(ctx.state, callback=make_decision_callback(ctx), audit=ctx.audit)
    registry = build_registry(ctx, ws, thinking=thinking)
    ctx.loop = AgentLoop(
        ctx.llm, registry, ctx.history, ctx.state,
        max_steps=ctx.config.max_steps, on_event=make_on_event(ctx, ws, thinking),
        context_limit=ctx.config.context_limit,
        outputs_dir=ctx.outputs_dir, plans_dir=ctx.plans_dir,
    )


# ---------------------------------------------------------------------------
# 会话恢复（启动 --resume 与 /resume 共用）
# ---------------------------------------------------------------------------


def find_latest_session(workspace: Path) -> Path | None:
    """定位工作区最新会话文件（按文件名排序取末位，命名含时间戳）。"""
    sessions_dir = workspace / ".glaucous" / "sessions"
    if not sessions_dir.is_dir():
        return None
    files = sorted(sessions_dir.glob("*.jsonl"))
    return files[-1] if files else None


def resume_history(workspace: Path, resume_id: str | None, system_prompt: str,
                   renderer: ThemeRenderer) -> tuple[History, SessionState]:
    """恢复会话：不带参数取最新；前缀模糊匹配；失败回退新会话。

    state 重置 plan/per-action（策略不跨会话持久化）；恢复后 system prompt
    用传入版本（启动时构建，不重建——避免注入段闪变，Day4 D6）。
    """
    sessions_dir = workspace / ".glaucous" / "sessions"
    if resume_id == "latest" or resume_id is None:
        session_file = find_latest_session(workspace)
        if session_file is None:
            renderer.note("未找到可恢复的会话，将开始新会话。")
            return History.create(system_prompt, workspace), SessionState()
    else:
        session_file = sessions_dir / f"{resume_id}.jsonl"
        if not session_file.exists():
            # 容错：按文件名模糊匹配（用户可只输入时间戳前缀）
            candidates = [p for p in sessions_dir.glob(f"{resume_id}*.jsonl")] if sessions_dir.is_dir() else []
            if not candidates:
                renderer.note(f"未找到会话 {resume_id}，将开始新会话。")
                return History.create(system_prompt, workspace), SessionState()
            session_file = candidates[-1]

    try:
        history, meta_workspace, warnings = History.load(session_file, system_prompt)
    except (ValueError, OSError) as exc:
        renderer.error(f"会话恢复失败（{exc}），将开始新会话。")
        return History.create(system_prompt, workspace), SessionState()

    renderer.info(f"🌅 已恢复上次会话（{session_file.stem}）")
    for warning in warnings:
        renderer.note(f"  ⚠ {warning}")
    if meta_workspace and meta_workspace.resolve() != workspace:
        renderer.note(f"  ⚠ 会话记录的工作区（{meta_workspace}）与当前不一致，上下文可能错位。")
    # 恢复预览：最近几条消息摘要，帮助用户接续上下文
    recent = history.view()[-RESUME_PREVIEW_MESSAGES:]
    for entry in recent:
        role = entry.get("role", "?")
        content = entry.get("content") or "（工具调用/无文本）"
        brief = content if len(content) <= 60 else content[:60] + "…"
        renderer.note(f"  · [{role}] {brief}")
    return history, SessionState()


# ---------------------------------------------------------------------------
# 输入层：prompt_toolkit 优先，三条降级路径回退 console.input（Day5 Plan §4.3）
# ---------------------------------------------------------------------------


# 路径补全的排除目录名（单层）；.glaucous/sessions 在路径层单独判定（v1.1 R2）
_PATH_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
# 单层候选上限（超大目录防卡），超出追加只读提示项（不可选中）
_PATH_MAX_CANDIDATES = 200


def _workspace_path_candidates(workspace: Path, arg: str) -> list:
    """工作区内路径候选（目录尾缀 / 便于继续深入；遍历异常静默返回空，v1.1 R2）。

    返回 (text, display, start_position) 三元组：text 为相对路径全文（供模糊匹配），
    start_position 只替换最后一段，候选插入后不丢失已输入的目录前缀。
    """
    from prompt_toolkit.completion import Completion

    arg = arg.strip()
    try:
        if arg.endswith("/") or arg == "":
            base, prefix = workspace / arg if arg else workspace, ""
        else:
            base, prefix = workspace / Path(arg).parent, Path(arg).name
        base = base.resolve()
        if workspace not in base.parents and base != workspace:
            return []
        candidates: list = []
        entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries:
            if entry.name in _PATH_EXCLUDE_DIRS:
                continue
            try:
                rel = entry.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel.startswith(".glaucous/sessions"):
                continue
            if prefix and not entry.name.lower().startswith(prefix.lower()):
                continue
            if entry.is_dir():
                candidates.append(Completion(rel + "/", start_position=-len(prefix), display=entry.name + "/"))
            else:
                candidates.append(Completion(rel, start_position=-len(prefix), display=entry.name))
            if len(candidates) >= _PATH_MAX_CANDIDATES:
                # 只读提示项：text 永不可匹配（用户继续输入缩小范围）
                candidates.append(Completion("\x00", display="…（更多，继续输入以缩小范围）"))
                break
        return candidates
    except Exception:  # noqa: BLE001 —— 权限/竞态等遍历异常：静默无候选，不阻断输入
        return []


def make_repl_completer(workspace: Path):
    """REPL 补全器（v1.1 R2，替换既有 WordCompleter）。

    - 命令段：/ 开头且无空格 → 命令名前缀/模糊补全（meta 取自 commands.COMMAND_META，
      单一数据源）；键入 / 立即列出全部（complete_while_typing 由 session 开启）；
    - 路径段：PATH_ARG_COMMANDS 命令 + 已输入空格 → 工作区内路径补全；
    - 其他段（自由对话）：不弹补全；依赖导入失败返回 None（降级无补全，不拒启动）。
    """
    try:
        from prompt_toolkit.completion import Completer, Completion

        # /quit 为 /exit 别名：补全层单独登记（摘要与 /exit 同源，COMMAND_META 不重复建条目）
        meta_map = {**COMMAND_META, "/quit": COMMAND_META["/exit"]}

        class _ReplCompleter(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if text.startswith("/") and " " not in text:
                    # 命令段：前缀匹配（需求 2）；键入 / 即列全部（空串前缀命中所有）
                    for name in meta_map:
                        if name.startswith(text):
                            yield Completion(name, start_position=-len(text), display_meta=meta_map[name])
                    return
                if " " not in text:
                    return  # 自由对话段不弹补全（需求 2 边界）
                cmd = text.split(" ", 1)[0]
                if cmd not in PATH_ARG_COMMANDS:
                    return
                arg = text.split(" ", 1)[1]
                for cand in _workspace_path_candidates(workspace, arg):
                    yield cand

        return _ReplCompleter()
    except Exception:  # noqa: BLE001 —— 补全器故障不拒启动：降级无补全输入
        return None


def make_prompt_session(workspace: Path):
    """构造 prompt_toolkit PromptSession（M3-UI PT_STYLE + R2 增强补全）。

    降级三条件命中返回 None：① GLAUCOUS_INPUT=plain（显式开关）；② stdin
    非 TTY（测试/管道）；③ prompt_toolkit 导入/构造失败（依赖损坏不拒启动，
    m3-day5 plan §4.3）——导入在此延迟，顶层不依赖 prompt_toolkit。
    """
    if os.environ.get("GLAUCOUS_INPUT", "").strip().lower() == "plain":
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        (workspace / ".glaucous").mkdir(exist_ok=True)
        try:
            input_history: FileHistory | None = FileHistory(workspace / ".glaucous" / "input_history")
        except OSError:
            input_history = None
        # PT_STYLE 为 None（prompt_toolkit 可导入但样式构造失败）时传默认样式；
        # complete_while_typing：键入 / 即弹命令列表（需求 2）
        return PromptSession(
            history=input_history,
            style=PT_STYLE,
            completer=make_repl_completer(workspace),
            complete_while_typing=True,
        )
    except Exception:  # noqa: BLE001 —— 输入层故障不拒启动：降级 console.input
        return None


# ---------------------------------------------------------------------------
# REPL 主循环
# ---------------------------------------------------------------------------


def _collapse_enabled() -> bool:
    """思考折叠开关（v1.1 R3）：stdout TTY 且未显式关闭（GLAUCOUS_COLLAPSE=off）。

    关闭/管道时不开 Live：事件维持现状逐条实时打印；turn_events 仍缓冲（/expand 可用）。
    """
    return sys.stdout.isatty() and os.environ.get("GLAUCOUS_COLLAPSE", "").strip().lower() != "off"


async def repl(workspace: Path, resume_id: str | None) -> None:
    """REPL：配置/注册表 → 组装 ReplContext → 输入循环（斜杠分派 / 任务执行）。"""
    theme = ThemeRenderer()
    try:
        config = load_config()
        entries, default = load_registry()
    except (ConfigError, RegistryError) as exc:
        # 错误文案含档案段名（如 [a]、[/]），必须 escape 防 markup 解析崩溃（代码评审 r1 B1）
        console.print(f"[glaucous.error]配置错误：{escape(str(exc))}[/]")
        raise SystemExit(1) from exc

    # M2 记忆注入（任务 2.1/2.2）+ M3 技能索引（任务 3.5）：现读现注入
    memory_store = MemoryStore(
        global_path=Path.home() / ".glaucous" / "memory.json",
        project_path=workspace / ".glaucous" / "memory.json",
    )
    skills = SkillRegistry(workspace)
    skills.scan()
    for warning in skills.warnings:
        theme.error(f"技能扫描告警：{warning}")
    system_prompt = build_system_prompt(
        workspace,
        rules=load_rules(workspace),
        memory=memory_store.load_injection(config.memory_top_n),
        skills=skills.index_text(),
    )
    if resume_id is not None:
        history, state = resume_history(workspace, resume_id, system_prompt, theme)
    else:
        history, state = History.create(system_prompt, workspace), SessionState()

    # 默认档案 → 客户端：重试通知经 theme（「↻ 重试中」，§4.2）；
    # on_usage 累加器（v1.1 R5）：归一化 payload 计入 turn_usage（本轮累计口径）；
    # counting_usage 门控：/compact 轮间压缩不计入（commands._cmd_compact 置 False）。
    # 闭包按名引用下方构造的 ctx（调用发生在轮内，届时已赋值）
    def _accumulate_usage(payload: dict[str, Any]) -> None:
        if not ctx.counting_usage:
            return
        acc = ctx.turn_usage
        acc["prompt"] += payload.get("prompt") or 0
        acc["completion"] += payload.get("completion") or 0
        for key in ("cache_hit", "cache_miss"):
            val = payload.get(key)
            if val is not None:
                if acc[key] is None:
                    acc[key] = 0  # 首次收到非 None 转 0 基线再累加；全程 None = 供应商无缓存数据
                acc[key] += val

    llm = LLMClient(config.profile, on_retry=theme.retry, on_usage=_accumulate_usage)
    theme.model_name = default

    # 思考折叠装配（v1.1 R3）：TTY 且未显式关闭才启用动态区；
    # 关闭/管道时 thinking 为 None：事件维持现状逐条实时打印，turn_events 仍缓冲（/expand 可用）
    thinking: ThinkingView | None = ThinkingView() if _collapse_enabled() else None

    ctx = ReplContext(
        workspace=workspace,
        config=config,
        registry_entries=entries,
        current_model=default,
        llm=llm,
        memory_store=memory_store,
        skills=skills,
        state=state,
        history=history,
        system_prompt=system_prompt,
        loop=None,
        audit=AuditLog(workspace / ".glaucous" / "audit.log"),
        renderer=theme,  # type: ignore[arg-type] —— 鸭子类型适配 M3-UI 主题渲染
        pipeline=None,
        outputs_dir=workspace / ".glaucous" / "outputs",
        plans_dir=workspace / ".glaucous" / "plans",
    )
    # Live 钩子注入：四阻塞点（ask/decision/plan_decision/retry）经 live_hooks 暂停/恢复动态区；
    # 折叠关闭/管道时保持字段默认的 no-op；retry 经 theme._live_hooks 同源接线（§3.2）
    if thinking is not None:
        ctx.live_hooks = {
            "pause": thinking.pause,
            "resume": thinking.resume,
            "step": thinking.note_step,  # 交互伪事件计数（N 口径含交互，§3.1）
        }
        theme._live_hooks = ctx.live_hooks
    rebuild_loop(ctx, thinking)
    render_banner(ctx.current_model, prompt_mode(ctx.state))
    # /view 专用沙箱（Workspace 轻量无状态，独立于重建循环）
    view_ws = Workspace(workspace, read_only_extra=config.read_only_extra)
    session = make_prompt_session(workspace)

    while True:
        # 输入区头部（rich，两路径共用）：模型 + ctx 占用行；模式段并入输入行前缀；
        # 模型名读 ctx.current_model（/model 切换后动态跟随）
        report = build_report(ctx.history.view(), config.context_limit)
        render_prompt_header(ctx.current_model, report)
        try:
            if session is not None:
                from prompt_toolkit.formatted_text import HTML

                prompt_html = HTML(f"<glaucous.title>🌊 {prompt_mode(ctx.state)} > </glaucous.title>")
                task = sanitize_input(await session.prompt_async(prompt_html)).strip()
            else:
                task = sanitize_input(console.input(f"🌊 {prompt_mode(ctx.state)} > ")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print("[glaucous.title]:waving_hand: 再见。[/]")
            return
        if not task:
            continue
        # 分派协议：斜杠输入本地处理，绝不发给 LLM（commands.py）
        if task in ("/exit", "/quit"):
            console.print("[glaucous.title]:waving_hand: 再见。[/]")
            return
        if task == "/view" or task.startswith("/view "):
            _cmd_view(task[5:].strip(), view_ws)
            continue
        if task.startswith("/"):
            result = await handle_command(task, ctx)
            if result == "exit":
                return
            continue
        # 任务轮开始：重置上一轮思考缓冲与用量（r4-B2 时机：轮末保留、轮首重置；
        # /expand 在本轮结束至此处之间可回看上一轮）
        reset_turn_buffers(ctx)
        if thinking is not None:
            thinking.count = 0
            thinking.start()  # 启动失败内部降级实时打印，本轮不再尝试（§3.3）
        # 自然终答已流式打印（补收尾换行）；终止诊断由 diagnostic 事件交付
        ctx.stream_state["printed"] = False
        answer = None
        turn_ok = False
        try:
            answer = await ctx.loop.run(task)
            turn_ok = True
        except (KeyboardInterrupt, asyncio.CancelledError):
            # asyncio.run 下 SIGINT 以 CancelledError 形态穿透（Day2 Plan §8）：
            # loop 已完成悬空 call 善后，中断本轮继续会话；轮末收缩由 finally 兼顾（§3.3）
            console.print("[glaucous.muted]\n（已中断本轮，可继续输入新任务）[/]")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 顶层兜底：单轮失败不退出会话（异常路径收缩同样经 finally）
            console.print(f"[glaucous.error]\n✘ 本轮执行失败：{escape(str(exc))}[/]")
            continue
        finally:
            # 轮末渲染顺序固定（§3.1）：折叠摘要行 → R7 md 卡片 → R5 用量行；
            # 异常路径：收缩与用量行仍执行，卡片跳过；缓冲保留供 /expand 回看
            if thinking is not None:
                thinking.close(ctx.turn_usage)
            if turn_ok:
                if answer and ctx.stream_state["printed"]:
                    console.print()
                if answer and answer.strip() and session is not None:
                    # R7：TTY 且回答非空才追加 Markdown 卡片（管道纯文本已输出，不重复）；
                    # session 非 None 即 TTY 非降级（make_prompt_session 三条件），与箭头选择同口径。
                    # 偏离登记（S5）：概设 §8.4「正文不套面板」，用户决策的产品化呈现。
                    render_answer_card(answer)
            usage_text = _usage_line(ctx.turn_usage)
            if usage_text:
                # 管道模式同样打印（纯文本无样式）；数值为纯数字字符串无需 escape（§5.2）
                console.print(f"  [glaucous.muted]{usage_text}[/]")


def main(argv: list[str] | None = None) -> None:
    """CLI 入口：glaucous [--workspace DIR] [--resume [SESSION_ID]]。"""
    # ⏺/⎿/☁ 等 Unicode 符号在部分 Windows 终端（cp936 管道/重定向）下
    # 会触发 UnicodeEncodeError；errors="replace" 保证降级可读而非崩溃（FR-34）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="glaucous",
        description="Glaucous —— 雨过天青，海鸥滑翔，代码自有清凉。CLI 编程智能体。",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="工作区目录（默认当前目录）",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        default=None,
        help="恢复会话：不带参数恢复最新会话，或指定会话 ID（时间戳前缀）",
    )
    args = parser.parse_args(argv)
    # 统一 resolve 为绝对路径：与 prompts.py 的 resolve 基准一致，
    # 保证 grep 的 relative_to 输出与 system prompt 中的工作区信息稳定
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        console.print(f"[glaucous.error]工作区不存在或不是目录：{escape(str(workspace))}[/]")
        raise SystemExit(1)
    try:
        asyncio.run(repl(workspace, args.resume))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
