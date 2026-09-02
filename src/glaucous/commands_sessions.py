"""会话管理命令（v1.1-M3，FR-46~50；自 commands.py 拆出，spec §四）。

/sessions 列表/搜索/切换、/rename 重命名、/fork 分叉、/stats 统计卡、
/resume 会话内恢复。cli 依赖（rebuild_loop/resume_history）保持函数内
延迟导入——monkeypatch.setattr(cli, ...) 的测试注入经 cli 门面生效。
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .agent.state import SessionState
from .context.history import History

if TYPE_CHECKING:
    from .commands import ReplContext
    from .sessions.index import SessionEntry


def _switch_blocked(ctx: "ReplContext") -> bool:
    """切换保护（FR-50，r1-B1 生命周期）：turn_active 置位期间拒绝切换。"""
    if ctx.turn_active:
        ctx.renderer.error("本轮任务执行中，无法切换会话。")
        return True
    return False


def _git_dirty(workspace: Path) -> bool:
    """git status --porcelain 非空 → 有未提交修改（FR-50）；失败/非 Git 静默 False。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def _note_uncommitted(ctx: "ReplContext") -> None:
    """切换/恢复后的未提交修改提示（FR-50；非 Git 工作区静默跳过）。"""
    if _git_dirty(ctx.workspace):
        ctx.renderer.note("⚠ 工作区有未提交修改（可能来自其他会话），可 git status 检查（/rollback 待 M4）。")


def _restore_session_usage(ctx: "ReplContext", session_id: str) -> None:
    """切换/恢复后的 token 累计恢复（r2-S10：索引只存合计，历史累计计入 ↑ 侧）。"""
    entry = ctx.session_index.find_by_id(session_id) if ctx.session_index else None
    ctx.session_usage = {"prompt": entry.token_used if entry else 0, "completion": 0}


def _entry_file(entry: "SessionEntry") -> Path:
    """索引条目 → 会话文件路径：用户级优先，degraded 会话回退 workspace 旧路径（r1-S5）。"""
    from .sessions.paths import project_hash, sessions_root

    user_level = sessions_root() / project_hash(Path(entry.workspace)) / f"{entry.id}.jsonl"
    if user_level.exists():
        return user_level
    return Path(entry.workspace) / ".glaucous" / "sessions" / f"{entry.id}.jsonl"


def _render_session_list(
    ctx: "ReplContext",
    entries: list["SessionEntry"],
    *,
    title: str,
    show_workspace: bool,
) -> None:
    """会话列表卡（FR-47）：名称/更新时间（相对）/消息数/token（/工作区尾段）。"""
    from rich.markup import escape

    from .theme import console, make_card

    if not entries:
        ctx.renderer.note("暂无会话。")
        return

    def _rel_time(iso: str) -> str:
        try:
            delta = datetime.now() - datetime.fromisoformat(iso)
        except ValueError:
            return iso
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds} 秒前"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        return f"{seconds // 86400} 天前"

    table = make_card(f":open_file_folder: {title}")
    for e in entries:
        label = e.name or e.id
        ws_tail = f" · {e.workspace.replace(chr(92), '/').rstrip('/').rsplit('/', 1)[-1]}" if show_workspace else ""
        table.add_row(
            f"[glaucous.title]{escape(label)}[/]",
            f"[glaucous.sub]{escape(_rel_time(e.updated_at))} · {e.message_count} 条 · {_fmt_tokens_short(e.token_used)} tokens{escape(ws_tail)}[/]",
            f"[glaucous.muted]{escape(e.id)}[/]",
        )
    console.print(table)


def _fmt_tokens_short(n: int) -> str:
    """token 短格式（<1000 原样，≥1000 k 单位，与 ui.thinking._fmt_tokens 同口径）。"""
    return str(n) if n < 1000 else f"{n / 1000:.1f}k"


async def _cmd_resume(ctx: "ReplContext", arg: str) -> bool:
    """会话内恢复：复用启动 resume_history 逻辑（不带参取最新、前缀模糊匹配）。"""
    from .cli import rebuild_loop, resume_history

    if _switch_blocked(ctx):
        return True
    history, state = resume_history(
        ctx.workspace, arg.strip() or "latest", ctx.system_prompt, ctx.renderer
    )
    ctx.history = history
    ctx.state = state
    ctx.last_budget = None
    ctx.renderer.last_budget = None
    ctx.session_events.clear()  # v1.1 F4：恢复的是历史会话，不携带思考缓冲
    _restore_session_usage(ctx, history.session_id)  # r2-S10：token 累计从索引恢复
    from .commands import begin_turn

    begin_turn(ctx)
    rebuild_loop(ctx)
    _note_uncommitted(ctx)
    return True


async def _cmd_sessions(ctx: "ReplContext", arg: str) -> bool:
    """会话列表 / 搜索 / 切换（FR-47；spec §4.1 四态消解）。"""
    if ctx.session_index is None:
        ctx.renderer.note("会话索引不可用（降级模式）。")
        return True
    kw = arg.strip()
    if not kw:
        here = str(ctx.workspace.resolve())
        entries = [e for e in ctx.session_index.all_sessions() if e.workspace == here]
        _render_session_list(ctx, entries, title="会话列表（当前项目）", show_workspace=False)
        ctx.renderer.note("[a] 全部项目 · /sessions <kw> 搜索 · /sessions <id> 切换")
        return True
    if kw == "a":
        _render_session_list(ctx, ctx.session_index.all_sessions(), title="会话列表（全部项目）", show_workspace=True)
        return True
    # id 消解（r1-S2 三态）：精确/前缀唯一 → 切换；多命中 → 候选列表；零命中 → 名称消解
    entry = ctx.session_index.find_by_prefix(kw, ctx.workspace)
    if entry is not None:
        if _switch_blocked(ctx):
            return True
        return await _switch_to_session(ctx, _entry_file(entry))
    candidates = ctx.session_index.prefix_candidates(kw, ctx.workspace)
    if candidates:
        _render_session_list(ctx, candidates, title=f"id 前缀 {kw!r} 多命中", show_workspace=True)
        ctx.renderer.note(f"{len(candidates)} 个候选，请用更长前缀切换")
        return True
    # 名称消解（用户实测反馈 2026-08-30）：精确同名唯一 → 切换；子串搜索仅展示
    exact_name = [e for e in ctx.session_index.search(kw) if e.name == kw]
    if len(exact_name) == 1:
        if _switch_blocked(ctx):
            return True
        return await _switch_to_session(ctx, _entry_file(exact_name[0]))
    if len(exact_name) > 1:
        _render_session_list(ctx, exact_name, title=f"同名会话 {kw!r} 多命中", show_workspace=True)
        ctx.renderer.note(f"{len(exact_name)} 个同名会话，请用 id 前缀切换")
        return True
    results = ctx.session_index.search(kw)
    if not results:
        ctx.renderer.note(f"未找到匹配会话：{kw}")
        return True
    _render_session_list(ctx, results, title=f"搜索「{kw}」", show_workspace=True)
    return True


async def _switch_to_session(ctx: "ReplContext", session_file: Path) -> bool:
    """切换会话共用流程（FR-50：只恢复对话不动文件；r2-S10 恢复 token 累计）。

    v1.1-M3 交付后对齐（r1-S8 作者确认）：切换后 state 重置为启动默认
    （SessionState()），与 /resume 既有语义统一——授权策略/模式不跨会话延续。
    History.load 失败（索引陈旧指向已删文件等，r1-S5）→ 报错保持当前会话。
    """
    from .cli import rebuild_loop

    try:
        history, meta_workspace, warnings = History.load(session_file, ctx.system_prompt)
    except (ValueError, OSError) as exc:
        ctx.renderer.error(f"会话切换失败（{exc}），保持当前会话。")
        return True
    for warning in warnings:
        ctx.renderer.note(f"  ⚠ {warning}")
    if meta_workspace and meta_workspace.resolve() != ctx.workspace:
        ctx.renderer.note(f"  ⚠ 会话记录的工作区（{meta_workspace}）与当前不一致，上下文可能错位。")
    ctx.history = history
    ctx.state = SessionState()  # r1-S8 确认：与 /resume 语义统一
    ctx.last_budget = None
    ctx.renderer.last_budget = None
    ctx.session_events.clear()
    _restore_session_usage(ctx, history.session_id)
    rebuild_loop(ctx)
    ctx.renderer.info(f"已切换到会话 {history.session_id}")
    _note_uncommitted(ctx)
    return True


async def _cmd_rename(ctx: "ReplContext", arg: str) -> bool:
    """重命名当前会话（FR-46）：同步索引；空参报用法。"""
    name = arg.strip()
    if not name:
        ctx.renderer.note("用法：/rename <name>")
        return True
    if ctx.session_index is None:
        ctx.renderer.note("会话索引不可用（降级模式）。")
        return True
    final_name = ctx.session_index.touch(ctx.history.session_id, ctx.workspace, name=name)
    ctx.renderer.info(f"当前会话已重命名为「{final_name}」")
    return True


async def _cmd_fork(ctx: "ReplContext", arg: str) -> bool:
    """分叉当前会话（FR-48 收窄语义：另存为，从当前状态分叉；spec §4.3）。"""
    from .cli import rebuild_loop

    if _switch_blocked(ctx):
        return True
    src = ctx.history.session_file
    if src is None or not src.exists():
        ctx.renderer.error("当前会话文件不存在，无法分叉。")
        return True
    from .sessions.index import SessionEntry
    from .sessions.paths import project_dir

    try:
        new_file = History.create_session_file(ctx.workspace, session_dir=project_dir(ctx.workspace))
    except OSError as exc:
        # r2-B1：入口创建失败（degraded 环境下 mkdir 抛出）→ 报错保持原会话，不击穿 REPL
        ctx.renderer.error(f"创建分叉会话失败：{exc}（保持当前会话）")
        return True
    try:
        lines = src.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        # r1-B2：IO 失败报错保持原会话（不击穿 REPL）
        ctx.renderer.error(f"读取当前会话失败，无法分叉：{exc}")
        return True
    if not lines:
        # r1-B2：空文件路径兜底（create 的 meta 落盘为尽力而为，前提不严格成立）
        ctx.renderer.error("当前会话文件为空，无法分叉。")
        return True
    try:
        meta = json.loads(lines[0])
        meta["session_id"] = new_file.stem  # meta 行 session_id 替换为新 id（其余行原样）
        lines[0] = json.dumps(meta, ensure_ascii=False)
    except (json.JSONDecodeError, IndexError) as exc:
        ctx.renderer.error(f"当前会话 meta 损坏，无法分叉：{exc}")
        return True
    try:
        new_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        ctx.renderer.error(f"写入分叉会话失败：{exc}（保持当前会话）")
        return True

    old_entry = ctx.session_index.find_by_id(ctx.history.session_id) if ctx.session_index else None
    base_name = (old_entry.name if old_entry else "") or "会话"
    new_name = arg.strip() or f"{base_name}-fork"
    usage = ctx.session_usage
    if ctx.session_index is not None:
        ctx.session_index.upsert(SessionEntry(
            id=new_file.stem,
            name=new_name,
            workspace=str(ctx.workspace.resolve()),
            created_at=(old_entry.created_at if old_entry else datetime.now().isoformat(timespec="seconds")),
            updated_at=datetime.now().isoformat(timespec="seconds"),
            message_count=len(ctx.history.messages),
            token_used=usage["prompt"] + usage["completion"],
        ))

    try:
        history, meta_workspace, warnings = History.load(new_file, ctx.system_prompt)
    except (ValueError, OSError) as exc:
        # r1-B2：加载失败报错保持原会话（半写文件留存供排查）
        ctx.renderer.error(f"分叉会话加载失败：{exc}（保持当前会话）")
        return True
    for warning in warnings:
        ctx.renderer.note(f"  ⚠ {warning}")
    ctx.history = history  # session_usage 继承当前值（决策 3）；state 重置（r1-S8 统一口径）
    ctx.state = SessionState()
    rebuild_loop(ctx)
    ctx.renderer.info(f"🕊 已分叉到新会话 {history.session_id}（原会话保留，可 /sessions 切回）")
    return True


async def _cmd_stats(ctx: "ReplContext") -> bool:
    """会话与全局统计卡（FR-49；spec §4.4）。"""
    from rich.markup import escape

    from .sessions.stats import approval_distribution, global_totals, role_distribution
    from .theme import console, make_card

    roles = role_distribution(ctx.history.messages)
    usage = ctx.session_usage
    entry = ctx.session_index.find_by_id(ctx.history.session_id) if ctx.session_index else None

    def _dist_lines(dist: dict[str, dict[str, int]]) -> list[str]:
        if not dist:
            return ["（无审批记录）"]
        lines = []
        for decision, agents in sorted(dist.items()):
            total = sum(agents.values())
            detail = " · ".join(f"{agent} {n}" for agent, n in sorted(agents.items()))
            lines.append(f"{decision}：共 {total}（{detail}）")
        return lines

    card = make_card(":bar_chart: 会话统计")
    card.add_row("会话", f"[glaucous.title]{escape((entry.name if entry else '') or ctx.history.session_id)}[/]")
    card.add_row("消息分布", " · ".join(f"{role} {n}" for role, n in sorted(roles.items())) or "（空）")
    card.add_row(
        "token 累计",
        f"↑{_fmt_tokens_short(usage['prompt'])} ↓{_fmt_tokens_short(usage['completion'])} tokens",
    )
    if entry:
        card.add_row("活跃时长", f"{entry.created_at} → {entry.updated_at}")
    for line in _dist_lines(approval_distribution([ctx.workspace / ".glaucous" / "audit.log"])):
        card.add_row("决策分布", f"[glaucous.sub]{escape(line)}[/]")
    console.print(card)

    if ctx.session_index is None:
        return True
    index, _corrupted = ctx.session_index.load()
    totals = global_totals(index)
    audit_paths = [
        Path(project["workspace"]) / ".glaucous" / "audit.log"
        for project in (index.get("projects") or {}).values()
        if project.get("workspace")
    ]
    gcard = make_card(":globe_with_meridians: 全局聚合")
    gcard.add_row(
        "汇总",
        f"{totals['sessions']} 个会话 · {totals['messages']} 条消息 · {_fmt_tokens_short(totals['tokens'])} tokens",
    )
    for line in _dist_lines(approval_distribution(audit_paths)):
        gcard.add_row("决策分布", f"[glaucous.sub]{escape(line)}[/]")
    console.print(gcard)
    return True
