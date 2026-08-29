"""CLI REPL：装配聚合 + prompt_toolkit 输入层 + 渲染卡片接线（Day 5 体验与扩展）。

产出（计划表任务 3.1~3.7 的 CLI 面）：
- ReplContext 聚合全部可重建组件；/clear、/resume 整体替换后经
  rebuild_loop 重建（回调经 ctx 间接引用，不捕获旧对象，Day5 Plan D8）；
- prompt_toolkit 输入层（跨会话历史/斜杠补全/状态栏），三条降级路径
  （非 TTY / 导入失败 / GLAUCOUS_INPUT=plain）回退 input()（§4.3）；
- 三处交互回调（方案确认/审批/提问）改经 Renderer 卡片；事件渲染经
  Renderer（rich 主题，ui/theme.py 单一出口）；
- 模型注册表接入：启动经 llm/registry 取默认档案，/model 切换见 commands。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .agent.state import POLICY_AUTO_APPROVE, POLICY_PER_ACTION, SessionState
from .commands import ReplContext, handle_command
from .config import ConfigError, load_config
from .context.history import History
from .extensions.memory import MemoryStore
from .extensions.rules import load_rules
from .extensions.skills import SkillRegistry
from .llm.client import LLMClient
from .llm.registry import RegistryError, load_registry
from .permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from .permission.modes import MODE_BUILD
from .permission.risk import Risk
from .permission.workspace import Workspace
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
from .ui.renderer import Renderer
from .ui.theme import make_console

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


def prompt_symbol(state: SessionState) -> str:
    """模式化提示符：build 追加审批策略缩写，提醒当前授权语义。"""
    if state.mode == MODE_BUILD:
        policy = "每次审批" if state.approval_policy == POLICY_PER_ACTION else "auto"
        return f"🌊 build·{policy} > "
    return "🌊 plan > "


# ---------------------------------------------------------------------------
# 交互回调（一律经 ctx 间接引用——/clear、/resume 替换组件后自动跟随，D8）
# ---------------------------------------------------------------------------


def make_ask_callback(ctx: ReplContext):
    """ask_user 终端实现（任务 2.3）：提问卡 + 候选列表 + 序号/自由文本回答。

    EOF/Ctrl+C 返回 None → 工具回喂「用户未响应」控制信号（非交互环境不挂死）。
    """

    def ask(question: str, options: list[str]) -> str | None:
        ctx.renderer.ask_card(question, options)
        try:
            raw = sanitize_input(input("  回答（输入候选序号或自由文本）: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        return raw

    return ask


def make_decision_callback(ctx: ReplContext):
    """审批决策回调：审批卡（DANGEROUS 陶土红边）+ 选项输入。

    per-action 弹选项；auto-approve 守卫在 gate 内先行处理；
    DANGEROUS 无「同意同类型」选项（不可批量放行）。
    """

    def decide(action: ApprovalAction) -> ApprovalDecision:
        dangerous = action.risk == Risk.DANGEROUS
        risk_note = "破坏性操作（不可批量放行）" if dangerous else ""
        ctx.renderer.approval_card(
            action.kind, action.target, action.risk, action.detail or "", risk_note, dangerous
        )
        while True:
            try:
                if dangerous:
                    raw = sanitize_input(input("  [a] 同意  [c] 拒绝(附理由): ")).strip()
                else:
                    raw = sanitize_input(input("  [a] 同意  [b] 同意同类型  [c] 拒绝(附理由): ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ApprovalDecision(choice="reject", reason="用户中断审批")
            if raw in ("a", "A", "y", "Y"):
                return ApprovalDecision(choice="approve")
            if not dangerous and raw in ("b", "B"):
                return ApprovalDecision(choice="approve_type")
            if raw in ("c", "C", "n", "N"):
                reason = sanitize_input(input("  拒绝理由（可留空）: ")).strip() or None
                return ApprovalDecision(choice="reject", reason=reason)
            print("  无效输入，请重试。")

    return decide


def prompt_plan_decision(plan: str, renderer: Renderer) -> PlanDecision:
    """方案确认卡后读入三选一决策；非法输入重问；Ctrl+C 视为③继续讨论。"""
    renderer.plan_card(plan)
    while True:
        try:
            raw = sanitize_input(input("  请选择 [1/2/3]（③可附加反馈，格式：3 反馈内容）: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=None)
        if not raw:
            continue
        choice, _, feedback = raw.partition(" ")
        if choice == CHOICE_KEEP_PLANNING:
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=feedback.strip() or None)
        if choice in (CHOICE_BUILD_PER_ACTION, CHOICE_BUILD_AUTO_APPROVE):
            return PlanDecision(choice=choice, feedback=feedback.strip() or None)
        print("  无效选择，请输入 1、2 或 3。")


def make_confirm_callback(ctx: ReplContext):
    """submit_plan 三选一回调：卡片呈现 + 状态切换接线（经 ctx.state，D8）。"""

    def confirm(plan: str) -> PlanDecision:
        decision = prompt_plan_decision(plan, ctx.renderer)
        if decision.choice == CHOICE_BUILD_PER_ACTION:
            ctx.state.enter_build(POLICY_PER_ACTION)
        elif decision.choice == CHOICE_BUILD_AUTO_APPROVE:
            ctx.state.enter_build(POLICY_AUTO_APPROVE)
        return decision

    return confirm


# ---------------------------------------------------------------------------
# 工具装配与循环重建
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
    registry.register(AskUserTool(ask=make_ask_callback(ctx)))
    # M2 任务 2.5/2.7：L0 落盘回取 + 方案回读
    registry.register(ReadOutputTool(ctx.outputs_dir))
    registry.register(ReadPlanTool(ctx.plans_dir))
    # M3 任务 3.5：技能惰性加载通道（两段式：索引已注入，正文经此取回）
    registry.register(LoadSkillTool(ctx.skills))

    registry.register(SubmitPlanTool(confirm=make_confirm_callback(ctx), plans_dir=ctx.plans_dir))
    return registry


def make_on_event(ctx: ReplContext):
    """loop 事件回调：经 ctx.renderer 渲染；缓存 budget（状态栏数据源）。"""

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            ctx.stream_state["printed"] = True
        if event == "budget":
            ctx.last_budget = payload
        policy = ctx.state.approval_policy if ctx.state.mode == MODE_BUILD else None
        ctx.renderer.render(event, payload, policy=policy, mode=ctx.state.mode)

    return on_event


def rebuild_loop(ctx: ReplContext) -> None:
    """重建管线与主循环（启动装配与 /clear、/resume 共用入口）。

    state 可能已被整体替换（/clear 重置、/resume 恢复）：管线随新 state
    重建，回调经 ctx 间接引用自动跟随（闭包不捕获旧对象，D8）；
    重建后旧 loop 对象不再被任何入口持有。
    """
    ws = Workspace(ctx.workspace, read_only_extra=ctx.config.read_only_extra)
    ctx.pipeline = ApprovalPipeline(ctx.state, callback=make_decision_callback(ctx), audit=ctx.audit)
    registry = build_registry(ctx, ws)
    ctx.loop = AgentLoop(
        ctx.llm, registry, ctx.history, ctx.state,
        max_steps=ctx.config.max_steps, on_event=make_on_event(ctx),
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
                   renderer: Renderer) -> tuple[History, SessionState]:
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

    renderer.note(f"🌅 已恢复上次会话（{session_file.stem}）")
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
# 输入层：prompt_toolkit 优先，三条降级路径回退 input()（Day5 Plan §4.3）
# ---------------------------------------------------------------------------


def make_prompt_session(ctx: ReplContext):
    """构造 prompt_toolkit PromptSession；降级条件命中返回 None。

    降级三条件：① GLAUCOUS_INPUT=plain（显式开关）；② stdin 非 TTY
    （测试/管道）；③ prompt_toolkit 导入失败（依赖损坏不拒启动）。
    """
    if os.environ.get("GLAUCOUS_INPUT", "").strip().lower() == "plain":
        return None
    if not sys.stdin.isatty():
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
    except ImportError:
        return None
    try:
        history_dir = Path.home() / ".glaucous"
        history_dir.mkdir(parents=True, exist_ok=True)

        def toolbar() -> str:
            policy = ctx.state.approval_policy if ctx.state.mode == MODE_BUILD else None
            return ctx.renderer.toolbar_text(ctx.state.mode, policy)

        return PromptSession(
            history=FileHistory(str(history_dir / "repl_history")),
            completer=WordCompleter(SLASH_COMMANDS),
            bottom_toolbar=toolbar,
        )
    except Exception:  # noqa: BLE001 —— 输入层故障不拒启动：降级 input()
        return None


async def read_line(ctx: ReplContext, session) -> str:
    """读取一行输入：两路径同一出口，REPL 主循环不感知差异。"""
    message = prompt_symbol(ctx.state)
    if session is not None:
        return sanitize_input(await session.prompt_async(message))
    # input() 是阻塞调用：交线程池执行，不占事件循环（与审批回调的 input() 一致）
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, lambda: input(message))
    return sanitize_input(raw)


# ---------------------------------------------------------------------------
# REPL 主循环
# ---------------------------------------------------------------------------


async def repl(workspace: Path, resume_id: str | None) -> None:
    """REPL：配置/注册表 → 组装 ReplContext → 输入循环（斜杠分派 / 任务执行）。"""
    renderer = Renderer(make_console())
    try:
        config = load_config()
        entries, default = load_registry()
    except (ConfigError, RegistryError) as exc:
        # 错误文案含档案段名（如 [a]、[/]），必须禁用 markup 防吞字/防 MarkupError（代码评审 r1 B1）
        renderer.console.print(f"配置错误：{exc}", style="glaucous.error", markup=False)
        raise SystemExit(1) from exc

    # M2 记忆注入（任务 2.1/2.2）+ M3 技能索引（任务 3.5）：现读现注入
    memory_store = MemoryStore(
        global_path=Path.home() / ".glaucous" / "memory.json",
        project_path=workspace / ".glaucous" / "memory.json",
    )
    skills = SkillRegistry(workspace)
    skills.scan()
    for warning in skills.warnings:
        renderer.error(f"技能扫描告警：{warning}")
    system_prompt = build_system_prompt(
        workspace,
        rules=load_rules(workspace),
        memory=memory_store.load_injection(config.memory_top_n),
        skills=skills.index_text(),
    )
    if resume_id is not None:
        history, state = resume_history(workspace, resume_id, system_prompt, renderer)
    else:
        history, state = History.create(system_prompt, workspace), SessionState()

    # 默认档案 → 客户端：重试通知经 renderer（「↻ 重试中」，§4.2）
    llm = LLMClient(config.profile, on_retry=renderer.retry)
    renderer.model_name = default

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
        renderer=renderer,
        pipeline=None,
        outputs_dir=workspace / ".glaucous" / "outputs",
        plans_dir=workspace / ".glaucous" / "plans",
    )
    rebuild_loop(ctx)
    renderer.banner()
    session = make_prompt_session(ctx)

    while True:
        try:
            task = (await read_line(ctx, session)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            renderer.console.print("  🌅 再见。", style="glaucous.dim")
            return
        if not task:
            continue
        # 分派协议：斜杠输入本地处理，绝不发给 LLM（commands.py）
        if task.startswith("/"):
            result = await handle_command(task, ctx)
            if result == "exit":
                return
            continue
        ctx.stream_state["printed"] = False
        try:
            answer = await ctx.loop.run(task)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # asyncio.run 下 SIGINT 以 CancelledError 形态穿透（Day2 Plan §8）：
            # loop 已完成悬空 call 善后，中断本轮继续会话
            renderer.note("（已中断本轮，可继续输入新任务）")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 顶层兜底：单轮失败不退出会话
            renderer.warn_card(f"本轮执行失败：{exc}")
            continue

        # 自然终答已流式打印（补收尾换行）；终止诊断由 diagnostic 事件交付
        if answer and ctx.stream_state["printed"]:
            renderer.console.print()


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
        print(f"工作区不存在或不是目录：{workspace}", file=sys.stderr)
        raise SystemExit(1)
    try:
        asyncio.run(repl(workspace, args.resume))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
