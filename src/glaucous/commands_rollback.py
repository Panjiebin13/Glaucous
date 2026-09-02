"""回退与上下文档位命令（自 commands.py 拆出，v1.1 评审重构）。

/rollback：一键回退文件到历史 checkpoint（可同时回退对话上下文，v1.1-M4 FR-42）。
/context：调整上下文窗口大小（三档 128K/512K/1M，用户验收反馈 2026-08-31）。
cli 依赖（rebuild_loop/select_with_arrows）与 commands.CONTEXT_TIERS 保持
函数内延迟导入——monkeypatch.setattr(cli, ...) 的测试注入经 cli 门面生效。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import ReplContext


async def _cmd_rollback(ctx: "ReplContext") -> bool:
    """一键回退（v1.1-M4，FR-42；spec §3.4）。

    流程：非 Git/空列表降级 → 列表箭头选择 → 变更清单确认卡 → store.rollback
    （只回文件）→ 上下文二问（默认否）：是 → History.truncate_to（锚校验）+
    rebuild_loop。阻塞交互遵守 live_hooks pause/resume 协议（v1.1 R3）。
    """
    from rich.markup import escape

    from .checkpoint.git_snapshots import GitError
    from .cli import rebuild_loop, select_with_arrows
    from .context.history import ContextAnchorMismatch
    from .theme import console, make_card

    store = ctx.checkpoint_store
    if store is None or not store.available:
        ctx.renderer.note(
            store.unavailable_reason() if store is not None else "checkpoint 不可用（未装配）。"
        )
        return True
    if ctx.turn_active:
        # 任务轮进行中拒绝回退（与 FR-50 切换保护同语义；「拒绝并回退」走审批卡路径）
        ctx.renderer.error("本轮任务执行中，无法回退。")
        return True
    cps = store.list()
    if not cps:
        ctx.renderer.note("暂无可用 checkpoint。")
        return True

    ctx.live_hooks["pause"]()
    try:
        # 步骤 3：列表选择（新→旧）
        options = [f"#{cp.seq} · {cp.created_at} · {cp.task[:40]}" for cp in cps]
        idx = select_with_arrows("选择要回退到的 checkpoint：", options)
        if idx is None:
            ctx.renderer.note("已取消回退。")
            return True
        cp = cps[idx]
        # 步骤 4：变更清单确认卡
        try:
            changes = store.preview_changes(cp)
        except GitError as exc:
            ctx.renderer.error(f"无法计算变更清单：{exc}")
            return True
        restore_n = sum(1 for c in changes if c["status"] in ("M", "D"))
        remove_n = sum(1 for c in changes if c["status"] == "A")
        console.print()
        table = make_card(f":hourglass_flowing_sand: 回退到 checkpoint #{cp.seq}")
        table.add_row("任务", f"[glaucous.title]{escape(cp.task[:60])}[/]")
        table.add_row("将还原", f"{restore_n} 个已修改/删除的文件")
        table.add_row("将移除", f"{remove_n} 个 checkpoint 之后新增的文件")
        console.print(table)
        for item in changes[:10]:
            mark = "还原" if item["status"] in ("M", "D") else "移除"
            console.print(f"  [glaucous.sub]{mark} {escape(item['path'])}[/]")
        if len(changes) > 10:
            console.print(f"[glaucous.muted]  …共 {len(changes)} 项，已截断展示[/]")
        confirm = select_with_arrows("确认回退？", ["确认回退", "取消"])
        if confirm != 0:
            ctx.renderer.note("已取消回退。")
            return True
        # 步骤 5：文件回退（只回文件，不动上下文）
        try:
            changes = store.rollback(cp)
        except GitError as exc:
            # spec §五：工作树可能已部分还原，显式告知优于假装成功
            ctx.renderer.error(f"回退失败，请用 git status 检查工作区：{exc}")
            return True
        failed = [c["path"] for c in changes if c.get("failed")]
        summary = f"已回退到 checkpoint #{cp.seq}（{len(changes)} 项变更）"
        if failed:
            # S5：列出未能移除的路径（≤5 条 + 溢出计数）
            shown = "、".join(failed[:5]) + (f" 等 {len(failed)} 项" if len(failed) > 5 else "")
            summary += f"；未能移除：{shown}"
        ctx.renderer.info(summary)
        # 上下文二问（默认否）
        ctx_choice = select_with_arrows(
            "是否同时回退对话上下文？", ["否（保留对话，默认）", "是（截断到该轮入口）"]
        )
        if ctx_choice == 1:
            try:
                ctx.history.truncate_to(cp.message_count, cp.anchor_digest)
            except ContextAnchorMismatch as exc:
                ctx.renderer.note(f"对话上下文已变更，本次仅回退文件：{exc}")
                return True
            except OSError as exc:
                ctx.renderer.error(f"文件已回退但对话截断失败：{exc}")
                return True
            ctx.last_budget = None  # 占用条随截断后的历史重算（spec §3.4）
            rebuild_loop(ctx)       # 与 /clear 同一重建路径（D8）
            ctx.renderer.note("对话上下文已回退到该轮入口。")
            ctx_note = "（对话上下文已同时截断到该轮入口）"
        else:
            ctx.renderer.note("对话上下文已保留，模型仍记得后续操作。")
            ctx_note = "（对话上下文保留）"
        # 用户验收反馈（2026-08-31）：回退动作写入上下文——此前回退不写历史，
        # 模型「不知道已回退」。以系统标记的 user 消息记录，模型据此感知文件状态变化；
        # 该消息会成为下一轮入口锚（正常）。选“是”截断后，上下文 = 截断历史 + 本记录。
        restore_paths = [c["path"] for c in changes if c["status"] in ("M", "D")]
        remove_paths = [c["path"] for c in changes if c["status"] == "A"]
        ctx.history.push_user(
            f"[系统] 用户执行了 /rollback，已回退到 checkpoint #{cp.seq}（该轮任务：{cp.task[:40]}）："
            f"还原 {len(restore_paths)} 个文件（{', '.join(restore_paths[:5])}"
            + (" 等" if len(restore_paths) > 5 else "") + f"），"
            f"移除 {len(remove_paths)} 个文件（{', '.join(remove_paths[:5])}"
            + (" 等" if len(remove_paths) > 5 else "") + f"）。{ctx_note}"
        )
        return True
    finally:
        ctx.live_hooks["resume"]()


async def _cmd_context(ctx: "ReplContext", arg: str) -> bool:
    """调整上下文窗口大小（用户验收反馈 2026-08-31：三档 128K/512K/1M，最大 1M）。

    无参 → 箭头选择三档；`/context <n|名>` 直接切换。切换后以 dataclasses.replace
    生成新 Config（frozen 不可原地改）赋给 ctx.config，并 rebuild_loop 使 loop 的
    压缩/预算阈值生效（D8）；占用条与 /compact 读 ctx.config.context_limit 自动跟随。
    """
    from dataclasses import replace

    from .cli import rebuild_loop, select_with_arrows
    from .commands import CONTEXT_TIERS

    current = ctx.config.context_limit

    def _label(limit: int, name: str) -> str:
        mark = "● " if limit == current else "  "
        return f"{mark}{name}（{limit:,} tokens）"

    target: int | None = None
    raw = arg.strip().lower()
    if raw:
        for i, (limit, name) in enumerate(CONTEXT_TIERS, 1):
            if raw in (str(i), name.lower()):
                target = limit
                break
        if target is None:
            ctx.renderer.error("无效档位。可选：1(128K) / 2(512K) / 3(1M)。")
            return True
    else:
        ctx.live_hooks["pause"]()
        try:
            idx = select_with_arrows(
                "选择上下文窗口大小：", [_label(l, n) for l, n in CONTEXT_TIERS]
            )
        finally:
            ctx.live_hooks["resume"]()
        if idx is None:
            ctx.renderer.note("已取消。")
            return True
        target = CONTEXT_TIERS[idx][0]

    if target == current:
        ctx.renderer.info(f"上下文已是 {target:,} tokens，无变化。")
        return True
    ctx.config = replace(ctx.config, context_limit=target)
    ctx.last_budget = None  # 占用条随新上限重算（下一轮头部刷新）
    rebuild_loop(ctx)       # 重建 loop 使压缩/预算阈值生效（D8：闭包经 ctx 间接引用）
    ctx.renderer.info(f"上下文窗口已调整为 {target:,} tokens。")
    return True
