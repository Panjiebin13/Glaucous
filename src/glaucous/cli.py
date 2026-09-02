"""CLI REPL 装配层与兼容门面（v1.1 评审重构：自 1878 行巨石文件拆分）。

职责收敛（其余迁出至 ui 子包与 sessions 子包）：
- 装配：ReplContext 组装、build_registry 工具装配、rebuild_loop 循环重建
  （/clear、/resume 整体替换后经 ctx 重建，回调经 ctx 间接引用，不捕获旧
  对象，Day5 Plan D8）；
- REPL 主循环：输入循环（斜杠分派 / 任务执行）与 main 入口。

兼容门面（re-export）：历史版本 cli.py 定义的符号继续可从本模块导入——
测试经 monkeypatch.setattr(cli, ...) 注入假对象、commands.py 与
spec/pipeline.py 经延迟导入 from .cli import ... 消费，门面属性是这些
路径的稳定锚点，不可删除（详见各符号的来源模块）：
- ui.render_events：render_event / _thinking_line
- ui.thinking：ThinkingView / _usage_line / _fmt_tokens
- ui.interact：sanitize_input / select_with_arrows / prompt_plan_decision
- ui.view：_cmd_view
- ui.input：make_prompt_session / make_repl_completer
- ui.callbacks：make_on_event / make_ask_callback / make_decision_callback /
  flush_text_segment / run_managed_turn / thinking_enter / thinking_exit /
  ThemeRenderer
- sessions.resume：resume_history / find_latest_session
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .agent.state import MODE_PLAN, POLICY_PER_ACTION, SessionState
from .agent.subagent import SubagentRunner
from .checkpoint.store import CheckpointStore
from .commands import ReplContext, handle_command, begin_turn
from .config import ConfigError, load_config
from .context.budget import BudgetReport, build_report
from .extensions.memory import MemoryStore
from .extensions.rules import load_rules
from .extensions.skills import SkillRegistry
from .llm.client import LLMClient
from .llm.registry import RegistryError, load_registry
from .permission.approval import ApprovalPipeline, AuditLog
from .permission.workspace import Workspace
from .tools.base import ToolRegistry
from .tools.files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from .tools.interactive import AskUserTool
from .tools.memory_tool import MemorySaveTool
from .tools.output import ReadOutputTool
from .tools.planning import (
    CHOICE_APPROVE,
    CHOICE_FEEDBACK,
    PlanDecision,
    ReadPlanTool,
    SubmitPlanTool,
)
from .tools.search import GrepTool
from .tools.shell import BashTool
from .tools.skill_tool import LoadSkillTool
from .tools.spawn_agent import SpawnAgentTool
from .tools.spec_tool import ReadSpecTool
from .sessions.index import SessionIndex, derive_name
from .sessions.paths import create_session_history, migrate_legacy_sessions
from .sessions.resume import find_latest_session, resume_history
from .ui.prompts import build_system_prompt

# ---- 兼容门面（re-export）：稳定锚点，勿删（见模块 docstring） -----------------
from .ui.render_events import render_event, _thinking_line  # noqa: F401
from .ui.thinking import ThinkingView, _fmt_tokens, _usage_line  # noqa: F401
from .ui.interact import (  # noqa: F401
    _arrow_mode,
    prompt_plan_decision,
    sanitize_input,
    select_with_arrows,
)
from .ui.view import _cmd_view  # noqa: F401
from .ui.input import make_prompt_session, make_repl_completer  # noqa: F401
from .ui.callbacks import (  # noqa: F401
    ThemeRenderer,
    flush_text_segment,
    make_ask_callback,
    make_decision_callback,
    make_on_event,
    run_managed_turn,
    thinking_enter,
    thinking_exit,
)

# rich 渲染：Console/色板单一出口（theme.py），动态内容一律 escape 防 markup 注入
from .theme import console, escape, make_card, render_answer_card


def render_banner(model_name: str, mode: str) -> None:
    """启动 Banner（主题设计 §2.1）：卡片化呈现，附加项 A 追加模型/模式行。

    make_card 的框内标题栏即 §2.1 mockup 的 ┌─ 标题 ─┐ 形态；副标语走
    glaucous.sub（海盐青斜体），操作提示走 glaucous.muted（晴空灰）。
    Banner 为启动快照：/model 切换后不刷新（数据源 ctx.current_model，S8 口径）。
    """
    table = make_card(":cloud: Glaucous · coding agent（v1.1 正式版）")
    table.add_row("[glaucous.sub]雨过天青，海鸥滑翔，代码自有清凉[/]")
    table.add_row("[glaucous.muted]输入任务开始对话，/help 查看命令，/exit 退出。默认 Build 自动放行（DANGEROUS 仍单独确认），/plan 切只读研究。[/]")
    table.add_row(f"[glaucous.muted]当前模型 {escape(model_name)} · 模式 {escape(mode)}[/]")
    console.print(table)


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
    from .theme import ctx_ring

    ring, level_style = ctx_ring(report.percent)
    console.print(
        f"\n[glaucous.muted]{escape(model_name)}[/]  "
        f"[{level_style}]{ring}[/] [glaucous.muted]{report.used // 1000}k/{report.limit // 1000}k tokens[/]"
    )


# ---------------------------------------------------------------------------
# 工具装配与循环重建（m3-day5：ReplContext 驱动，/clear、/resume 共用）
# ---------------------------------------------------------------------------


def build_registry(
    ctx: ReplContext,
    ws: Workspace,
    thinking: ThinkingView | None = None,
    decision_callback=None,
    on_event=None,
) -> ToolRegistry:
    """装配全量工具：只读四件 + 双写 + submit_plan + 交互/记忆/回取 + load_skill + spawn_agent。

    权限管线注入 registry（dispatch 层统一审批）；交互回调经 ctx 注入；
    read_output/read_plan 的目录由系统派生（无沙箱面，Day4 Plan D8）。
    v1.1-M2：spawn_agent 仅注册给主 agent（子 registry 由 runner 派生时排除，
    FR-64 防嵌套）；runner 组装依赖决策回调与主 on_event（由 rebuild_loop 传入，
    与主 pipeline / 主 loop 同源同一闭包）。
    """
    registry = ToolRegistry()
    reader = ReadFileTool(ws)
    registry.register(reader)
    registry.register(ListDirTool(ws, reader=reader))
    registry.register(GrepTool(ws, reader=reader))
    registry.register(BashTool(ws))
    registry.register(WriteFileTool(ws, reader=reader, on_skill_write=lambda: ctx.skills.scan()))
    registry.register(EditFileTool(ws, reader=reader, on_skill_write=lambda: ctx.skills.scan()))
    registry.set_approval_pipeline(ctx.pipeline)

    # M2 任务 2.2/2.3：记忆写入与用户求助（回调经 ctx：伪事件记录 + Live 钩子，v1.1 R3/R6）
    registry.register(MemorySaveTool(ctx.memory_store))
    registry.register(AskUserTool(ask=make_ask_callback(ctx)))
    # M2 任务 2.5/2.7：L0 落盘回取 + 方案回读
    registry.register(ReadOutputTool(ctx.outputs_dir))
    registry.register(ReadPlanTool(ctx.plans_dir))
    # M3 任务 3.5：技能惰性加载通道（两段式：索引已注入，正文经此取回）
    registry.register(LoadSkillTool(ctx.skills))

    # v1.1-M2：spawn_agent 派发通道（FR-60~64，概设 §8）。仅主 agent 注册；
    # decision_callback/on_event 与主 pipeline/主 loop 同源，避免双闭包漂移
    runner = SubagentRunner(
        llm=ctx.llm,
        parent_registry=registry,
        state=ctx.state,
        audit=ctx.audit,
        decision_callback=decision_callback,
        workspace=ctx.workspace,
        rules=load_rules(ctx.workspace),
        max_steps=ctx.config.max_steps,
        context_limit=ctx.config.context_limit,
        outputs_dir=ctx.outputs_dir,
        plans_dir=ctx.plans_dir,
        on_event=on_event,
        ctx=ctx,
    )
    registry.register(SpawnAgentTool(runner))
    # v1.1-M5（决策 7）：runner 挂账 ctx——SpecPipeline 评审/验收复用同一 runner；
    # read_spec 常备注册（FR-56，子 registry 派生自然继承，r1-S9）
    ctx.subagent_runner = runner
    registry.register(ReadSpecTool(ctx.workspace))

    def confirm(plan: str) -> PlanDecision:
        """二选一交互（v1.1-M1，FR-38）：批准执行 / 修改意见。

        状态切换收敛（spec §4.3）：active state（v1.1-M2：子 agent 派发期间为
        子副本 ctx.active_state，主 agent 路径回退 ctx.state——不捕获实例，
        /clear、/resume 整体替换后仍正确，D8）下批准才 enter_build()（策略维持现状，
        FR-39 口头确认出口；切换反馈由 loop 统一出口的 mode_changed 事件承担，
        r1-B2 方案 c），BUILD 下批准不触碰状态；删除旧版按选项落位策略的
        两处 enter_build——授权策略仅经 /build 显式改变。
        v1.1 R6：TTY 非降级时箭头选择（取消 = 修改意见，feedback 落「用户取消」，
        r2-S4：PlanDecision 无 reason 字段）；R3：阻塞交互前后暂停/恢复思考区，
        并记录 plan_decision 伪事件。
        v1.1-M2（spec §4.2，r1-B1/r2-B1）：子 agent 派发期间先打归属行（🕊 子 agent
        任务摘要），两条决策路径（箭头/数字回退）都可见；归属行必须在 pause()
        之后打印——折叠区擦除协议会吞掉 pause 之前刚打的行（r2-B1 实证）。
        """
        ctx.live_hooks["pause"]()
        try:
            # v1.1-M2（spec §4.2）：归属行在 pause 后、try 内打印（r3-S1：
            # 打印异常不至于绕过 finally resume，与 ask/decide 卡结构对齐）
            if ctx.active_agent != "主 agent":
                console.print(
                    f"[glaucous.sub]  🕊 子 agent（任务：{escape(ctx.active_task[:40])}）[/]"
                )
            decision: PlanDecision | None = None
            if _arrow_mode():
                idx = select_with_arrows("请选择：", ["批准执行", "提出修改意见"])
                if idx == 0:
                    decision = PlanDecision(choice=CHOICE_APPROVE)
                else:  # 选二或取消（Esc）：修改意见，取消意图统一落 feedback
                    decision = PlanDecision(
                        choice=CHOICE_FEEDBACK,
                        feedback="用户取消" if idx is None else None,
                    )
            else:
                decision = prompt_plan_decision(plan)
            # v1.1-M2：批准作用于 active state（子 agent 派发期间 = 子副本，
            # 主 agent 路径为 ctx.state——None 哨兵动态回退，不捕获实例，D8）
            active_state = ctx.active_state or ctx.state
            if decision.choice == CHOICE_APPROVE and active_state.mode == MODE_PLAN:
                active_state.enter_build()  # FR-39 口头确认：批准即回 Build（策略不变）
            flush_text_segment(ctx)  # §4.2 触发点 2：伪事件前保序落账正文段
            ctx.session_events.append(("plan_decision", {
                "summary": ("方案确认 → 批准" if decision.choice == CHOICE_APPROVE
                            else "方案确认 → 修改意见")
                + (f"（反馈：{decision.feedback}）" if decision.feedback else ""),
            }))
            ctx.live_hooks.get("step", lambda: None)()  # N 口径：交互伪事件计入思考步数（§3.1）
            return decision
        finally:
            ctx.live_hooks["resume"]()

    registry.register(SubmitPlanTool(confirm=confirm, plans_dir=ctx.plans_dir))
    return registry


def rebuild_loop(ctx: ReplContext, thinking: ThinkingView | None = None) -> None:
    """重建管线与主循环（启动装配与 /clear、/resume 共用入口）。

    state 可能已被整体替换（/clear 重置、/resume 恢复）：管线随新 state
    重建，回调经 ctx 间接引用自动跟随（闭包不捕获旧对象，D8）；
    重建后旧 loop 对象不再被任何入口持有。thinking 为思考区动态区（折叠关闭时为 None）。
    v1.1-M3 交付后修复：thinking 未显式传入时从 ctx.thinking 取——/clear、/resume、
    /fork、/sessions 切换的命令层调用均不传参，若重建为 thinking=None 则重建后
    loop 事件全降级直打、思考区计数归零、终答不缓冲不渲染卡片（用户实测复现）。
    v1.1-M2：决策回调与 on_event 先建一份，同源传入主 pipeline、spawn runner
    与主 loop（spawn_agent 报告经主 on_event 的 sub_* 通道渲染）。
    """
    if thinking is None:
        thinking = getattr(ctx, "thinking", None)
    # git 兜底区探测（用户决策 2026-08-31）：工作区是 Git 仓库（checkpoint 可用）
    # 时区内写尽可能放行——区内文件写免审、区内危险命令降级 WRITE；
    # 非 Git 无兜底 → 维持严格定级。available 惰性探测一次即缓存，重建开销可忽。
    git_backed = bool(
        getattr(ctx, "checkpoint_store", None) and ctx.checkpoint_store.available
    )
    ws = Workspace(ctx.workspace, read_only_extra=ctx.config.read_only_extra, git_backed=git_backed)
    on_event = make_on_event(ctx, ws, thinking)
    decision_callback = make_decision_callback(ctx)
    ctx.pipeline = ApprovalPipeline(ctx.state, callback=decision_callback, audit=ctx.audit)
    registry = build_registry(
        ctx, ws, thinking=thinking, decision_callback=decision_callback, on_event=on_event
    )
    ctx.loop = AgentLoop(
        ctx.llm, registry, ctx.history, ctx.state,
        max_steps=ctx.config.max_steps, on_event=on_event,
        context_limit=ctx.config.context_limit,
        outputs_dir=ctx.outputs_dir, plans_dir=ctx.plans_dir,
        # v1.1-M4（FR-40/43，spec B2）：checkpoint 仅注入主 loop（子 agent loop
        # 不产生子 checkpoint）；本轮入口 seq 经回调外泄到 ctx，供审批卡消费
        checkpoint_store=getattr(ctx, "checkpoint_store", None),
        on_checkpoint=lambda cp: setattr(ctx, "turn_checkpoint_seq", cp.seq),
    )


# ---------------------------------------------------------------------------
# REPL 主循环
# ---------------------------------------------------------------------------


def _collapse_enabled() -> bool:
    """思考折叠开关（v1.1 R3）：stdout TTY 且未显式关闭（GLAUCOUS_COLLAPSE=off）。

    关闭/管道时不开 Live：事件维持现状逐条实时打印；会话缓冲仍记录（/expand 可用）。
    """
    return sys.stdout.isatty() and os.environ.get("GLAUCOUS_COLLAPSE", "").strip().lower() != "off"


def consume_pending_task(ctx: ReplContext) -> str | None:
    """消费 /skill 组装的待执行任务（F3）：取出并置 None，仅驱动一次（当次生效）。

    独立函数便于单测覆盖「repl 消费后置 None」（spec §五）；repl 斜杠分派后
    检查 pending_task 非空 → 取出落入任务轮（与用户输入任务同一入口）。
    """
    task, ctx.pending_task = ctx.pending_task, None
    return task


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
    # v1.1-M3：会话索引装配（FR-45；损坏/缺失 → 重建降级）+ 旧会话迁移（FR-51）——
    # 先于会话创建/恢复（迁移后 --resume latest 才能找到刚迁移的会话）；
    # 写入失败经 on_error 告警（r1-B1：尽力而为 ≠ 静默）
    session_index = SessionIndex(on_error=lambda msg: theme.note(f"⚠ {msg}"))
    migrated = migrate_legacy_sessions(workspace, session_index)
    for line in migrated:
        theme.note(line)
    moved_count = sum(1 for line in migrated if line.startswith("已迁移"))  # r1-S3：仅计成功迁移
    if moved_count:
        theme.note(f"已迁移 {moved_count} 个旧会话到用户级存储。")
    _index, corrupted = session_index.load()
    if corrupted:
        session_index.rebuild(workspace)
        theme.note("会话索引已重建（原索引缺失或损坏）。")

    if resume_id is not None:
        history, state = resume_history(workspace, resume_id, system_prompt, theme)
    else:
        # v1.1-M3（FR-44）：新建会话统一走用户级入口（r1-B3 存储收敛，spec §5.4）
        history, degraded = create_session_history(system_prompt, workspace)
        if degraded:
            theme.note("⚠ 用户级会话目录不可用，已降级到工作区旧路径。")
        state = SessionState()

    # 启动后首次登记/恢复 token 累计（r2-S10：从索引恢复，spec §5.1 步骤 4）
    entry = session_index.find_by_id(history.session_id)
    session_usage = {"prompt": entry.token_used if entry else 0, "completion": 0}
    session_index.touch(history.session_id, workspace)

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
    # 关闭/管道时 thinking 为 None：事件维持现状逐条实时打印，会话缓冲仍记录（/expand 可用）
    thinking: ThinkingView | None = ThinkingView() if _collapse_enabled() else None

    # v1.1-M4（FR-40/41）：checkpoint 存储装配——惰性探测（非 Git 工作区降级为
    # 不可用提示，不阻断启动）；保留数量可配（GLAUCOUS_CHECKPOINT_MAX_KEEP）
    audit = AuditLog(workspace / ".glaucous" / "audit.log")
    checkpoint_store = CheckpointStore(workspace, audit=audit, max_keep=config.checkpoint_max_keep)

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
        audit=audit,
        renderer=theme,  # type: ignore[arg-type] —— 鸭子类型适配 M3-UI 主题渲染
        pipeline=None,
        outputs_dir=workspace / ".glaucous" / "outputs",
        plans_dir=workspace / ".glaucous" / "plans",
        session_index=session_index,
        session_usage=session_usage,
        checkpoint_store=checkpoint_store,
    )
    # Live 钩子注入：四阻塞点（ask/decision/plan_decision/retry）经 live_hooks 暂停/恢复动态区；
    # 折叠关闭/管道时保持字段默认的 no-op；retry 经 theme._live_hooks 同源接线（§3.2）
    ctx.thinking = thinking  # v1.1-M3 交付后修复：供命令层 rebuild_loop 沿用
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
    # F1：模型名/技能名补全候选经闭包动态取值（/model 切换、技能创建后跟随，不缓存快照）
    session = make_prompt_session(
        workspace,
        model_names=lambda: list(ctx.registry_entries),
        skill_names=lambda: [info.name for info in ctx.skills.infos()],
        session_names=lambda: ctx.session_index.project_names(workspace) if ctx.session_index else [],
    )

    while True:
        # 输入区头部（rich，两路径共用）：模型 + ctx 占用行；模式段并入输入行前缀；
        # 模型名读 ctx.current_model（/model 切换后动态跟随）
        report = build_report(ctx.history.view(), ctx.config.context_limit)
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
            if not ctx.pending_task:
                continue
            # F3：/skill 组装的任务立即执行一轮（与用户输入任务同一入口），当次生效
            task = consume_pending_task(ctx)
        # 任务轮开始：轮级重置（F4 §4.3 begin_turn：清 turn_usage 与正文段缓冲，
        # 不动会话缓冲；session_events 仅 /clear、/resume 清空，/expand 回看全会话）
        begin_turn(ctx)
        if thinking is not None:
            thinking.start_turn()
            thinking.start()  # 启动失败内部降级实时打印，本轮不再尝试（§3.3）
        # 终答不自流式直出（F4：折叠时进缓冲、降级时已直出），轮末统一呈现；
        # 终止诊断由 diagnostic 事件必达交付
        ctx.stream_state["printed"] = False
        answer = None
        turn_ok = False
        # v1.1-M3（FR-50，r1-B1）：切换保护置位（复位在本轮 finally）
        ctx.turn_active = True
        # v1.1-M4（FR-43）：本轮入口 checkpoint 由 loop.run 入口 on_checkpoint 写入；
        # 轮开始先清哨兵（上轮残留不可消费）
        ctx.turn_checkpoint_seq = None
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
            # v1.1-M3：切换保护复位（r1-B1）+ 会话 token 累计与索引刷新（FR-45，尽力而为）
            ctx.turn_active = False
            ctx.turn_checkpoint_seq = None  # v1.1-M4：本轮入口哨兵生命周期终结（spec §3.5）
            usage_acc = ctx.session_usage
            usage_acc["prompt"] += ctx.turn_usage.get("prompt") or 0
            usage_acc["completion"] += ctx.turn_usage.get("completion") or 0
            if ctx.session_index is not None:
                ctx.session_index.touch(
                    ctx.history.session_id,
                    ctx.workspace,
                    auto_name=derive_name(task),  # 仅在 name 为空时生效（FR-46）
                    message_count=len(ctx.history.messages),
                    token_used=usage_acc["prompt"] + usage_acc["completion"],
                )
            # 轮末时序（F4 §4.4）：折叠摘要行 → 最终回答自缓冲一次性输出 + 🕊 卡片 →
            # 用量行；异常路径：收缩与用量行照常，正文段落账后清空（B3），不呈现。
            # close 后 was_active 已复位（R5）：先取判据再收缩
            was_active = thinking.was_active if thinking is not None else False
            if thinking is not None:
                thinking.close(ctx.turn_usage)
            if turn_ok:
                body = "".join(ctx.text_segment).strip()
                if body:
                    if was_active:
                        # v1.1 修订（用户反馈）：渲染前原文归思考过程（/expand 可回看），
                        # 最终输出仅呈现 🕊 md 卡片；落账在摘要行渲染后，不计入已显示 N（S9 口径）
                        ctx.session_events.append(("text_segment", {"text": body}))
                        if session is not None:
                            render_answer_card(body)
                    else:
                        # 降级/管道轮：正文已逐字直打，补收尾换行；TTY 降级仍渲染卡片（R7）
                        console.print()
                        if session is not None:
                            render_answer_card(body)
                elif ctx.stream_state["printed"]:
                    # 降级/管道轮（r1-B2 修复）：正文已逐字直打，仅补收尾换行；
                    # 不渲染卡片、不重复输出（§4.5：轮末仅用量行）。
                    # 诊断轮（_terminate）缓冲空且无 text 事件，自然跳过（§4.4 步骤 2）
                    console.print()
            else:
                # §4.4 步骤 5（B3）：异常轮正文段落账供 /expand 全会话，不呈现
                flush_text_segment(ctx)
            ctx.text_segment.clear()  # 终答不落账（§4.2），轮末清空兜底
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
