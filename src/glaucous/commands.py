"""斜杠命令处理器：分派协议 + 14 命令全集（任务 3.3，FR-31，概设 §8）。

设计要点（Day5 Plan §4.3）：
- 分派协议：以 "/" 开头的输入本地处理，绝不发给 LLM；未识别命令打印
  /help 可用列表（不误发）；
- ReplContext 是命令与重建循环的唯一通道：/clear、/resume 整体替换
  history/state 后经 rebuild_loop 重建 AgentLoop 与管线（回调经 ctx
  间接引用，不捕获旧对象，D8）；
- 审计：/plan、/build、/model <name> 成功切换经 AuditLog.record 追加
  事件（尽力而为，不阻断）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .agent.state import SessionState
from .config import Config
from .context import compactor
from .context.budget import build_report, estimate_messages
from .context.history import History
from .extensions.init_draft import render_draft, scan_workspace
from .extensions.memory import MemoryStore
from .extensions.rules import global_rules_path, project_rules_path
from .extensions.skills import SOURCE_LABEL, SkillRegistry
from .llm.client import LLMClient
from .llm.registry import ModelEntry, RegistryError, ping, resolve_profile
from .permission.approval import ApprovalPipeline, AuditLog
from .permission.modes import MODE_BUILD, MODE_PLAN, POLICY_AUTO_APPROVE, POLICY_PER_ACTION
from .ui.renderer import Renderer

# 记忆作用域与列表序号前缀（/memory 展示，FR-21）
MEMORY_SCOPES = ("project", "global")
_SCOPE_PREFIX = {"project": "p", "global": "g"}
_SCOPE_LABEL = {"project": "项目", "global": "全局"}

# 命令全集单一数据源（v1.1 打磨 R2-S10）：命令名 → 一句摘要。
# HELP_LINES 由它拼装；cli 补全器的 meta 也从本表导入（cli → commands 既有导入方向，
# 无循环导入）；/view <文件路径>、/expand 一并入表。
COMMAND_META: dict[str, str] = {
    "/help": "列出全部命令",
    "/plan": "切换到 Plan 研究模式（只读，产出分析与建议）",
    "/build": "切换到 Build 执行模式（默认自动放行，可选 per-action）",
    "/compact": "手动压缩上下文（L1 裁剪 + L2 摘要）",
    "/clear": "开始新会话（旧会话保留，可 /resume）",
    "/resume": "恢复会话：不带参取最新，id 支持前缀模糊匹配",
    "/model": "列出模型档案 / 切换档案（切换时连通性校验）",
    "/memory": "查看记忆（/memory add|del 管理）",
    "/rules": "查看全局/项目规则文件",
    "/skills": "列出技能（任务匹配自动生效，/skill 可手动调用）",
    "/skill": "手动调用技能（立即执行一轮任务）",
    "/init": "生成 glaucous.md 草稿（确认后写入）",
    "/stop": "优雅结束会话（会话已落盘）",
    "/exit": "退出会话",
    "/view": "查看工作区文件（按类型渲染 Markdown/代码/CSV）",
    "/expand": "回看本会话思考过程",
    "/collapse": "收起已展开的思考过程（/expand 的逆操作）",
}

# 展示形态（含参数占位与别名）；未列出的命令直接用命令名展示。/exit /quit 条目保留（附加项 C）。
_COMMAND_USAGE: dict[str, str] = {
    "/resume": "/resume [id]",
    "/model": "/model [name]",
    "/build": "/build [auto-approve|per-action]",
    "/exit": "/exit  /quit",
    "/view": "/view <文件路径>",
    "/skill": "/skill <名> [任务描述]",
}


def _build_help_lines() -> tuple[str, ...]:
    """由 COMMAND_META 拼装 /help 输出（单一数据源，列宽自动对齐）。"""
    width = max(len(_COMMAND_USAGE.get(name, name)) for name in COMMAND_META)
    return tuple(
        f"{_COMMAND_USAGE.get(name, name).ljust(width)} {COMMAND_META[name]}"
        for name in COMMAND_META
    )


# /help 输出：命令全集一行说明（未知命令兜底复用）
HELP_LINES = _build_help_lines()


@dataclass
class ReplContext:
    """REPL 可变聚合：斜杠命令与重建循环的唯一通道（Day5 Plan §3）。

    /clear、/resume 会整体替换 history/state/loop/pipeline；命令层与
    回调层一律经本对象间接引用（D8：闭包不捕获旧对象）。
    """

    workspace: Path
    config: Config
    registry_entries: dict[str, ModelEntry]
    current_model: str                # 当前模型档案名（状态栏数据源）
    llm: LLMClient
    memory_store: MemoryStore
    skills: SkillRegistry
    state: SessionState
    history: History
    system_prompt: str
    loop: AgentLoop | None            # 由 rebuild_loop 装配（启动即填充）
    audit: AuditLog
    renderer: Renderer
    pipeline: ApprovalPipeline | None  # 由 rebuild_loop 随 state 重建
    outputs_dir: Path
    plans_dir: Path
    last_budget: dict | None = None    # 最近一次 budget 事件 payload（状态栏数据源）
    # 内部：本轮是否已打印流式正文（repl 判断是否补收尾换行）
    stream_state: dict[str, bool] = field(default_factory=lambda: {"printed": False})
    # —— v1.1 F4：会话级思考过程缓冲（F4 语义变更：跨轮保留，仅 /clear、/resume 清空）——
    # 条目为 (event, payload)：① 非 text 的 on_event 事件；② 交互伪事件（ask/decision/
    # plan_decision）；③ 中间步正文段（"text_segment"，由 flush_text_segment 落账）。
    session_events: list = field(default_factory=list)
    # 当前段正文缓冲（仅内存，F4 §4.2）：text 增量累积于此，tool_start/伪事件落账前
    # flush；轮末为最终回答（输出后清空），异常轮落账后清空（§4.4 步骤 5）
    text_segment: list[str] = field(default_factory=list)
    # F3 /skill：待执行的任务文本（_cmd_skill 组装；repl 消费后置 None，仅驱动一次 run）
    pending_task: str | None = None
    # Live 区暂停/恢复钩子：阻塞交互进入前 pause、返回后 resume；
    # 折叠关闭/管道时由 repl 注入 no-op（默认即 no-op）
    live_hooks: dict[str, Any] = field(
        default_factory=lambda: {"pause": lambda: None, "resume": lambda: None}
    )
    # —— v1.1 打磨 R5：本轮用量累计（本轮累计口径，轮末渲染后保留至下轮开始）——
    turn_usage: dict[str, Any] = field(
        default_factory=lambda: {"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None}
    )
    # usage 计入门控：/compact 压缩期间置 False（压缩发生在轮间，不计入任务轮口径）
    counting_usage: bool = True


def begin_turn(ctx: ReplContext) -> None:
    """轮级状态重置（v1.1 F4，取代前批 reset_turn_buffers 语义）。

    清 turn_usage（保持 R5「本轮累计」口径，不跨轮累加）与当前正文段缓冲；
    **不动会话缓冲 session_events**（/expand 回看全会话，仅 /clear、/resume 清空）。
    调用点：repl 的两处任务轮入口（用户输入任务、/skill 消费）；轮计数器
    （thinking.count）由 repl 在 begin_turn 后同步清零（thinking 可能不存在）。
    """
    ctx.text_segment.clear()
    ctx.turn_usage.update({"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None})


def _policy_of(ctx: ReplContext) -> str | None:
    """状态行/卡片用的当前授权策略（仅 Build 模式附注）。"""
    return ctx.state.approval_policy if ctx.state.mode == MODE_BUILD else None


def _audit(ctx: ReplContext, **fields: Any) -> None:
    """审计事件追加（尽力而为）：统一附时间戳。"""
    event = {"at": datetime.now().isoformat(timespec="seconds")}
    event.update(fields)
    ctx.audit.record(event)


# ---------------------------------------------------------------------------
# 命令实现（每个命令一个协程，M4 可独立单测）
# ---------------------------------------------------------------------------


async def _cmd_help(ctx: ReplContext) -> bool:
    for line in HELP_LINES:
        ctx.renderer.note(line)
    return True


async def _cmd_plan(ctx: ReplContext) -> bool:
    if ctx.state.mode == MODE_PLAN:
        ctx.renderer.note("已处于 Plan 研究模式，无需切换。")
        return True
    ctx.state.enter_plan()
    _audit(ctx, event="mode_switch", to="plan", via="/plan")
    ctx.renderer.info("已进入 Plan 研究模式（只读，产出分析与建议；/build 或批准方案回切）。")
    return True


async def _cmd_build(ctx: ReplContext, arg: str = "") -> bool:
    """切换到 Build（v1.1-M1，FR-36）：无参仅切模式（策略维持现状），
    auto-approve / per-action 显式落位策略（spec §3.2）；非法参数不改状态。"""
    policy = arg.strip().lower()
    if policy and policy not in (POLICY_AUTO_APPROVE, POLICY_PER_ACTION):
        ctx.renderer.error("用法：/build [auto-approve|per-action]（无参数仅切换模式，策略维持现状）")
        return True
    was_build = ctx.state.mode == MODE_BUILD
    policy_changed = bool(policy) and policy != ctx.state.approval_policy
    ctx.state.enter_build(policy or None)
    note = "自动放行 + 底线守卫" if ctx.state.approval_policy == POLICY_AUTO_APPROVE else "每次操作审批"
    if was_build and not policy_changed:
        # 已在 Build 且无实际变化（无参或策略相同）：轻提示，不重复审计（与 /plan 对齐）
        ctx.renderer.note("已处于 Build 模式，授权策略不变。")
        return True
    _audit(ctx, event="mode_switch", to="build", policy=ctx.state.approval_policy, via="/build")
    if not was_build:
        ctx.renderer.info(f"已进入 Build 模式（{note}）。")
    else:
        ctx.renderer.info(f"授权策略已切换：{note}。")
    return True


async def _cmd_compact(ctx: ReplContext) -> bool:
    """手动压缩：先 trim_history 后 compact_history（复用 loop 守卫点同款函数）。

    v1.1 打磨 R5：压缩发生在轮间，其 LLM 用量不计入任务轮口径——
    压缩调用期间关 counting_usage 门控（try/finally 保证恢复）。
    """
    before = estimate_messages(ctx.history.view())
    trimmed = compactor.trim_history(ctx.history.messages)
    ctx.counting_usage = False
    try:
        ok = await compactor.compact_history(
            ctx.history.messages, ctx.llm, plans_dir=ctx.plans_dir
        )
    finally:
        ctx.counting_usage = True
    after = estimate_messages(ctx.history.view())
    l2_note = "L2 摘要成功" if ok else "L2 摘要失败，仅完成 L1 裁剪"
    ctx.renderer.note(f"压缩完成：约 {before} → {after} tokens（L1 裁剪 {trimmed} 条；{l2_note}）。")
    report = build_report(ctx.history.view(), ctx.config.context_limit)
    ctx.renderer.render_budget_report(report, ctx.state.mode, _policy_of(ctx))
    ctx.last_budget = {
        "used": report.used, "limit": report.limit,
        "percent": report.percent, "level": report.level,
    }
    return True


async def _cmd_clear(ctx: ReplContext) -> bool:
    """开新会话：新 JSONL + 状态重置 + 系统提示词现读重建 + 重建循环。"""
    # 延迟导入：cli 顶层导入本模块，反向引用只能函数内完成（避免模块环）
    from .cli import rebuild_loop
    from .extensions.rules import load_rules
    from .ui.prompts import build_system_prompt

    ctx.skills.scan()  # 技能索引刷新（规则/记忆同理现读重建）
    ctx.system_prompt = build_system_prompt(
        ctx.workspace,
        rules=load_rules(ctx.workspace),
        memory=ctx.memory_store.load_injection(ctx.config.memory_top_n),
        skills=ctx.skills.index_text(),
    )
    ctx.history = History.create(ctx.system_prompt, ctx.workspace)
    ctx.state = SessionState()
    ctx.last_budget = None
    ctx.renderer.last_budget = None
    ctx.session_events.clear()  # v1.1 F4：新会话无思考缓冲，/expand 回到空态提示
    begin_turn(ctx)
    rebuild_loop(ctx)
    ctx.renderer.info("已开始新会话（规则/记忆/技能索引已刷新；旧会话可用 /resume 找回）。")
    return True


async def _cmd_resume(ctx: ReplContext, arg: str) -> bool:
    """会话内恢复：复用启动 resume_history 逻辑（不带参取最新、前缀模糊匹配）。"""
    from .cli import rebuild_loop, resume_history

    history, state = resume_history(
        ctx.workspace, arg.strip() or "latest", ctx.system_prompt, ctx.renderer
    )
    ctx.history = history
    ctx.state = state
    ctx.last_budget = None
    ctx.renderer.last_budget = None
    ctx.session_events.clear()  # v1.1 F4：恢复的是历史会话，不携带思考缓冲
    begin_turn(ctx)
    rebuild_loop(ctx)
    return True


async def _cmd_model(ctx: ReplContext, arg: str) -> bool:
    name = arg.strip()
    if not name:
        ctx.renderer.note(f"模型档案（当前：{ctx.current_model}）：")
        for entry_name, entry in ctx.registry_entries.items():
            marker = "●" if entry_name == ctx.current_model else "○"
            key_ok = bool(os.environ.get(entry.api_key_env, "").strip())
            key_note = f"✓ {entry.api_key_env}" if key_ok else f"✗ 环境变量 {entry.api_key_env} 未设置"
            ctx.renderer.note(f"  {marker} {entry_name}: {entry.model} @ {entry.base_url} | {key_note}")
        ctx.renderer.note("切换：/model <name>（切换时做连通性校验）。")
        return True
    entry = ctx.registry_entries.get(name)
    if entry is None:
        ctx.renderer.error(f"未注册档案 {name!r}，可用：{'、'.join(ctx.registry_entries)}")
        return True
    ctx.renderer.note(f"正在校验档案 {name} 连通性（≤15s）…")
    ok, reason = await ping(entry)
    if not ok:
        ctx.renderer.error(f"切换失败：{reason}（保持当前档案 {ctx.current_model}）。")
        return True
    try:
        profile = resolve_profile(entry)
    except RegistryError as exc:
        ctx.renderer.error(f"切换失败：{exc}")
        return True
    previous = ctx.current_model
    ctx.llm.switch_profile(profile)
    ctx.current_model = name
    ctx.renderer.model_name = name
    _audit(ctx, event="model_switch", from_model=previous, to=name)
    ctx.renderer.info(f"已切换到模型档案 {name}（{entry.model}），后续请求即时生效。")
    return True


async def _cmd_memory(ctx: ReplContext, arg: str) -> bool:
    parts = arg.strip().split(maxsplit=2)
    if not parts:
        for scope in MEMORY_SCOPES:
            entries = ctx.memory_store.entries(scope)
            ctx.renderer.note(f"【{_SCOPE_LABEL[scope]}记忆】（{len(entries)} 条）")
            if not entries:
                ctx.renderer.note("  （空）")
                continue
            prefix = _SCOPE_PREFIX[scope]
            for idx, entry in enumerate(entries, 1):
                content = entry.get("content", "")
                category = entry.get("category", "")
                last_used = entry.get("last_used", "")
                ctx.renderer.note(f"  [{prefix}{idx}] {content} [{category}] ({last_used})")
        ctx.renderer.note("管理：/memory add <global|project> <内容> · /memory del <global|project> <序号>")
        return True

    sub = parts[0]
    if sub == "add":
        if len(parts) < 3:
            ctx.renderer.note("用法：/memory add <global|project> <内容>")
            return True
        scope, content = parts[1], parts[2].strip()
        if scope not in MEMORY_SCOPES:
            ctx.renderer.error(f"作用域应为 global 或 project（收到 {scope!r}）。")
            return True
        if not content:
            ctx.renderer.error("内容不能为空。")
            return True
        is_new = ctx.memory_store.add(content, scope)
        if is_new:
            ctx.renderer.info(f"已新增{_SCOPE_LABEL[scope]}记忆；下次会话或 /clear 后注入生效。")
        else:
            ctx.renderer.note("该条记忆已存在（已刷新最近使用时间）。")
        return True
    if sub == "del":
        if len(parts) < 3:
            ctx.renderer.note("用法：/memory del <global|project> <序号>（序号见 /memory 列表）")
            return True
        scope, raw_index = parts[1], parts[2].strip()
        if scope not in MEMORY_SCOPES:
            ctx.renderer.error(f"作用域应为 global 或 project（收到 {scope!r}）。")
            return True
        if not raw_index.isdigit() or int(raw_index) < 1:
            ctx.renderer.error("序号应为正整数（对应 /memory 列表中的编号）。")
            return True
        if ctx.memory_store.remove(scope, int(raw_index) - 1):
            ctx.renderer.info(f"已删除{_SCOPE_LABEL[scope]}记忆第 {raw_index} 条；下次会话或 /clear 后注入生效。")
        else:
            total = len(ctx.memory_store.entries(scope))
            ctx.renderer.error(f"序号越界：{_SCOPE_LABEL[scope]}记忆当前共 {total} 条。")
        return True
    ctx.renderer.note(f"未知子命令 /memory {sub}。用法：/memory [add|del] …")
    return True


async def _cmd_rules(ctx: ReplContext) -> bool:
    for label, path in (("全局", global_rules_path()), ("项目", project_rules_path(ctx.workspace))):
        if not path.is_file():
            ctx.renderer.note(f"【{label}规则】{path}（未创建）")
            continue
        ctx.renderer.note(f"【{label}规则】{path}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            ctx.renderer.error(f"  读取失败：{exc}")
            continue
        ctx.renderer.console.print(text.rstrip(), markup=False, highlight=False)
    return True


async def _cmd_skills(ctx: ReplContext) -> bool:
    """列出技能（v1.1 F2：去加载状态展示——惰性加载是设计使然，不产生误导）。"""
    infos = ctx.skills.infos()
    if not infos:
        ctx.renderer.note("未注册任何技能（内置/全局/项目目录均未发现 SKILL.md）。")
    else:
        ctx.renderer.note("已注册技能：")
        for info in infos:
            ctx.renderer.note(
                f"  {info.name} [{SOURCE_LABEL.get(info.source, info.source)}] {info.description}"
            )
    ctx.renderer.note("技能在任务匹配时自动生效；也可用 /skill <名> [任务描述] 手动调用。")
    for warning in ctx.skills.warnings:
        ctx.renderer.error(f"扫描告警：{warning}")
    return True


async def _cmd_skill(ctx: ReplContext, arg: str) -> bool:
    """手动调用技能（v1.1 F3）：组装任务文本写入 pending_task，由 repl 立即执行。

    当次生效：仅驱动一次 run()，不注入 system prompt；描述可省略（以技能
    正文本身作为任务指令）；名字与技能名完全相等（不做前缀/模糊匹配）。
    """
    parts = arg.strip().split(maxsplit=1)
    available = [info.name for info in ctx.skills.infos()]
    if not parts:
        ctx.renderer.note("用法：/skill <名> [任务描述]")
        ctx.renderer.note(f"可用技能：{'、'.join(available) or '（无）'}")
        return True
    name = parts[0]
    desc = parts[1].strip() if len(parts) > 1 else ""
    body = ctx.skills.skill_text(name)
    if body is None:
        ctx.renderer.error(f"未注册技能 {name!r}，可用：{'、'.join(available) or '（无）'}")
        return True
    ctx.pending_task = (
        "请按照以下技能的指令执行。\n\n"
        f"[技能 {name}]\n{body}\n\n用户任务：{desc or '按技能指令执行'}"
    )
    return True


async def _cmd_init(ctx: ReplContext) -> bool:
    from .cli import sanitize_input

    target = project_rules_path(ctx.workspace)
    if target.exists():
        ctx.renderer.note(f"{target} 已存在，不覆盖（请手工修订）。")
        return True
    _entries, features = scan_workspace(ctx.workspace)
    draft = render_draft(features)
    ctx.renderer.note("草稿如下（确认后才写入）：")
    ctx.renderer.console.print(draft.rstrip(), markup=False, highlight=False)
    try:
        raw = sanitize_input(input("  [y] 写入 / [n] 放弃: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raw = ""
    if raw in ("y", "yes"):
        try:
            target.write_text(draft, encoding="utf-8")
        except OSError as exc:
            ctx.renderer.error(f"写入失败：{exc}")
            return True
        ctx.renderer.info(f"已写入 {target}；下次会话或 /clear 后注入生效。")
    else:
        ctx.renderer.note("已放弃写入。")
    return True


async def _cmd_expand(ctx: ReplContext) -> bool:
    """回看本会话思考过程（v1.1 F4 语义：全会话重放，仅 /clear、/resume 清空）。

    条目重放：交互伪事件摘要、中间步正文段全文、tool_end 全量结果内容
    （§4.2：缓冲保留全量，重放可见完整内容），仅读不改状态。
    """
    from .cli import render_event  # 延迟导入：cli 顶层导入本模块，避免模块环（同 _cmd_clear）

    if not ctx.session_events:
        ctx.renderer.note("暂无可展开的思考过程。")
        return True
    ctx.renderer.note("── 思考过程（本会话）──")
    for event, payload in ctx.session_events:
        if event in ("ask", "decision", "plan_decision"):
            # 交互伪事件：非 on_event 事件，直接摘要展示（人机交互完整回看）
            ctx.renderer.note(f"  ❓ {event}: {payload.get('summary', payload)}")
        elif event == "text_segment":
            # 中间步正文段：全文重放（F4 §4.3 ③ 类条目）
            ctx.renderer.note("  💬 中间步正文：")
            for line in payload.get("text", "").splitlines() or [""]:
                ctx.renderer.note(f"    {line}")
        elif event == "tool_end":
            # 工具结果全量重放（不受摘要行数限制，spec §4.2 完整可见）
            result = payload.get("result")
            call = payload.get("call")
            name = getattr(call, "name", "?")
            args = getattr(call, "arguments", "") or ""
            brief = args if len(args) <= 80 else args[:80] + "…"
            ctx.renderer.note(f"  ⏺ {name} {brief}")
            content = (getattr(result, "content", "") or "") if result is not None else ""
            for line in content.splitlines() or [""]:
                ctx.renderer.note(f"    ⎿ {line}")
        else:
            render_event(event, payload, ctx.state)
    return True


async def _cmd_collapse(ctx: ReplContext) -> bool:
    """收起已展开的思考过程（v1.1 修订，用户反馈）：/expand 的逆操作。

    重印折叠摘要行收尾；已滚出屏的展开内容不回擦（行数受 rich 折行影响
    不准，回擦有残留风险，取舍为零风险收尾）。
    """
    ctx.renderer.note("💭 思考过程已收起 — /expand 可再次查看。")
    return True


async def _cmd_stop(ctx: ReplContext) -> str:
    # 输入阶段无运行中任务（REPL 串行）：会话 JSONL 已全量落盘，
    # /resume 即恢复通道（Day5 Plan D7）
    ctx.renderer.note("☁ 会话已保存。")
    return "exit"


# ---------------------------------------------------------------------------
# 分派入口
# ---------------------------------------------------------------------------


async def handle_command(line: str, ctx: ReplContext) -> bool | str:
    """斜杠命令分派：返回 True=已处理继续 REPL，"exit"=退出会话。

    未识别命令打印 /help 可用列表（不发给 LLM，分派协议兜底）。
    """
    if not line.startswith("/"):
        return False
    cmd, _, rest = line.strip().partition(" ")
    if cmd == "/help":
        return await _cmd_help(ctx)
    if cmd == "/plan":
        return await _cmd_plan(ctx)
    if cmd == "/build":
        return await _cmd_build(ctx, rest)
    if cmd == "/compact":
        return await _cmd_compact(ctx)
    if cmd == "/clear":
        return await _cmd_clear(ctx)
    if cmd == "/resume":
        return await _cmd_resume(ctx, rest)
    if cmd == "/model":
        return await _cmd_model(ctx, rest)
    if cmd == "/memory":
        return await _cmd_memory(ctx, rest)
    if cmd == "/rules":
        return await _cmd_rules(ctx)
    if cmd == "/skills":
        return await _cmd_skills(ctx)
    if cmd == "/skill":
        return await _cmd_skill(ctx, rest)
    if cmd == "/init":
        return await _cmd_init(ctx)
    if cmd == "/expand":
        return await _cmd_expand(ctx)
    if cmd == "/collapse":
        return await _cmd_collapse(ctx)
    # 附加项 C：/exit、/quit 分派分支已删除（cli repl 内联拦截为唯一路径）；
    # HELP_LINES 中条目保留（经 COMMAND_META/_COMMAND_USAGE）
    if cmd == "/stop":
        return await _cmd_stop(ctx)
    ctx.renderer.error(f"未知命令 {cmd}。可用命令：")
    for help_line in HELP_LINES:
        ctx.renderer.note(help_line)
    return True
