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
from typing import Any

from .agent.loop import AgentLoop
from .agent.state import POLICY_AUTO_APPROVE, POLICY_PER_ACTION, SessionState
from .commands import ReplContext, handle_command
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
# rich 渲染：Console/色板单一出口（theme.py），动态内容一律 escape 防 markup 注入
from rich.markup import escape
from .theme import (
    Markdown,
    PT_STYLE,
    console,
    ctx_ring,
    make_card,
    render_code_doc,
    render_csv_doc,
    render_markdown_doc,
    render_text_doc,
)

def render_banner() -> None:
    """启动 Banner（主题设计 §2.1）：卡片化呈现，文案保持既有三行不变。

    make_card 的框内标题栏即 §2.1 mockup 的 ┌─ 标题 ─┐ 形态；副标语走
    glaucous.sub（海盐青斜体），操作提示走 glaucous.muted（晴空灰）。
    """
    table = make_card(":cloud: Glaucous · coding agent（M3 体验与扩展）")
    table.add_row("[glaucous.sub]雨过天青，海鸥滑翔，代码自有清凉[/]")
    table.add_row("[glaucous.muted]输入任务开始对话，/help 查看命令，/exit 退出。Plan 只读探索，Build 写操作走审批。[/]")
    console.print(table)


# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要，M3 折叠升级）
RESULT_TAIL_LINES = 3

# markdown 文档卡片渲染的行数上限：read_file 打开 .md 时，内容行数 ≤ 此值才
# 渲染卡片（防长文档刷屏）；超长维持默认摘要并提示 /view 主动查看
MD_RENDER_MAX_LINES = 200

# resume 时回放的最近消息条数（仅 UI 摘要，History 本身全量加载）
RESUME_PREVIEW_MESSAGES = 6

# prompt_toolkit 补全的斜杠命令全集（14 个，含 /exit、/quit）
SLASH_COMMANDS = [
    "/help", "/plan", "/build", "/compact", "/clear", "/resume", "/model",
    "/memory", "/rules", "/skills", "/init", "/stop", "/exit", "/quit",
]


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
        """LLM 退避重试通知（晚霞橙，Day5 Plan §4.2 on_retry 钩子）。"""
        console.print(f"[glaucous.warn]  ↻ 重试中（第 {attempt} 次，预计等待 {delay:.0f}s）[/]")

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


def make_ask_callback():
    """ask_user 终端实现（任务 2.3）：提问卡 + 候选列表 + 序号/自由文本回答。

    EOF/Ctrl+C 返回 None → 工具回喂「用户未响应」控制信号（非交互环境不挂死）。
    """

    def ask(question: str, options: list[str]) -> str | None:
        console.print()
        table = make_card(":dove: 想请教你")
        # 问题正文走 Markdown（markdown.* 主题色板；方括号天然安全，无需 escape）
        if question.strip():
            table.add_row(Markdown(question))
        for i, option in enumerate(options, 1):
            table.add_row(f"[glaucous.title][{i}] {escape(option)}[/]")
        console.print(table)
        try:
            raw = sanitize_input(console.input("  [glaucous.sub]回答（输入候选序号或自由文本）: [/]")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        return raw

    return ask


def make_decision_callback():
    """审批三选项决策回调（per-action 弹三选项；auto-approve 守卫在 gate 内先行处理）。

    破坏性命令（DANGEROUS/区外写）用 ⚠ 警示 + 命令全文（主题色渲染）。
    """

    def decide(action: ApprovalAction) -> ApprovalDecision:
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
        while True:
            try:
                if dangerous:
                    raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[c] 拒绝(附理由): [/]")).strip()
                else:
                    raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[b] 同意同类型  \\[c] 拒绝(附理由): [/]")).strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return ApprovalDecision(choice="reject", reason="用户中断审批")
            if raw in ("a", "A", "y", "Y"):
                return ApprovalDecision(choice="approve")
            if not dangerous and raw in ("b", "B"):
                return ApprovalDecision(choice="approve_type")
            if raw in ("c", "C", "n", "N"):
                reason = sanitize_input(console.input("  [glaucous.sub]拒绝理由（可留空）: [/]")).strip() or None
                return ApprovalDecision(choice="reject", reason=reason)
            console.print("[glaucous.error]  无效输入，请重试。[/]")

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


def build_registry(ctx: ReplContext, ws: Workspace) -> ToolRegistry:
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

    # M2 任务 2.2/2.3：记忆写入与用户求助
    registry.register(MemorySaveTool(ctx.memory_store))
    registry.register(AskUserTool(ask=make_ask_callback()))
    # M2 任务 2.5/2.7：L0 落盘回取 + 方案回读
    registry.register(ReadOutputTool(ctx.outputs_dir))
    registry.register(ReadPlanTool(ctx.plans_dir))
    # M3 任务 3.5：技能惰性加载通道（两段式：索引已注入，正文经此取回）
    registry.register(LoadSkillTool(ctx.skills))

    def confirm(plan: str) -> PlanDecision:
        """三选一交互：卡片呈现 + 状态切换接线（经 ctx.state，D8）。"""
        decision = prompt_plan_decision(plan)
        if decision.choice == CHOICE_BUILD_PER_ACTION:
            ctx.state.enter_build(POLICY_PER_ACTION)
        elif decision.choice == CHOICE_BUILD_AUTO_APPROVE:
            ctx.state.enter_build(POLICY_AUTO_APPROVE)
        return decision

    registry.register(SubmitPlanTool(confirm=confirm, plans_dir=ctx.plans_dir))
    return registry


def make_on_event(ctx: ReplContext, ws: Workspace):
    """loop 事件回调：主题化渲染 + /view markdown 卡片 + budget 缓存（状态栏数据源）。"""

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            ctx.stream_state["printed"] = True
        if event == "budget":
            ctx.last_budget = payload
        if event == "tool_end" and _render_md_tool_end(payload, ws):
            return
        render_event(event, payload, ctx.state)

    return on_event


def rebuild_loop(ctx: ReplContext) -> None:
    """重建管线与主循环（启动装配与 /clear、/resume 共用入口）。

    state 可能已被整体替换（/clear 重置、/resume 恢复）：管线随新 state
    重建，回调经 ctx 间接引用自动跟随（闭包不捕获旧对象，D8）；
    重建后旧 loop 对象不再被任何入口持有。
    """
    ws = Workspace(ctx.workspace, read_only_extra=ctx.config.read_only_extra)
    ctx.pipeline = ApprovalPipeline(ctx.state, callback=make_decision_callback(), audit=ctx.audit)
    registry = build_registry(ctx, ws)
    ctx.loop = AgentLoop(
        ctx.llm, registry, ctx.history, ctx.state,
        max_steps=ctx.config.max_steps, on_event=make_on_event(ctx, ws),
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


def make_prompt_session(workspace: Path):
    """构造 prompt_toolkit PromptSession（M3-UI PT_STYLE + 斜杠命令补全）。

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
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory

        (workspace / ".glaucous").mkdir(exist_ok=True)
        try:
            input_history: FileHistory | None = FileHistory(workspace / ".glaucous" / "input_history")
        except OSError:
            input_history = None
        # PT_STYLE 为 None（prompt_toolkit 可导入但样式构造失败）时传默认样式
        return PromptSession(
            history=input_history,
            style=PT_STYLE,
            completer=WordCompleter(SLASH_COMMANDS),
        )
    except Exception:  # noqa: BLE001 —— 输入层故障不拒启动：降级 console.input
        return None


# ---------------------------------------------------------------------------
# REPL 主循环
# ---------------------------------------------------------------------------


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

    # 默认档案 → 客户端：重试通知经 theme（「↻ 重试中」，§4.2）
    llm = LLMClient(config.profile, on_retry=theme.retry)
    theme.model_name = default

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
    rebuild_loop(ctx)
    render_banner()
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
        # 自然终答已流式打印（补收尾换行）；终止诊断由 diagnostic 事件交付
        ctx.stream_state["printed"] = False
        try:
            answer = await ctx.loop.run(task)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # asyncio.run 下 SIGINT 以 CancelledError 形态穿透（Day2 Plan §8）：
            # loop 已完成悬空 call 善后，中断本轮继续会话
            console.print("[glaucous.muted]\n（已中断本轮，可继续输入新任务）[/]")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 顶层兜底：单轮失败不退出会话
            console.print(f"[glaucous.error]\n✘ 本轮执行失败：{escape(str(exc))}[/]")
            continue

        if answer and ctx.stream_state["printed"]:
            console.print()


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
