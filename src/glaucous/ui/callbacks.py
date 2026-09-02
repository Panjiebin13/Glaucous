"""REPL 交互回调层（自 cli.py 拆出，v1.1 评审重构）。

- flush_text_segment：正文段缓冲落账（F4 §4.2）；
- ThemeRenderer：commands.py 斜杠命令的渲染接口适配（M3-UI theme.py 单一出口）；
- make_ask_callback / make_decision_callback：ask_user 提问卡与审批三选项决策；
- run_managed_turn：Spec pipeline 任务轮壳（复刻 repl 任务轮时序）；
- make_on_event：loop 事件回调（正文缓冲 + 动态区滚动 + 会话缓冲）。

回调一律经 ReplContext 间接引用（D8：闭包不捕获旧对象）；阻塞交互前后经
live_hooks pause/resume 让位思考区动态区（v1.1 R3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..agent.state import POLICY_PER_ACTION
from ..context.budget import BudgetReport
from ..permission.approval import ApprovalAction, ApprovalDecision
from ..permission.risk import Risk
from ..sessions.index import derive_name
from ..theme import (
    Markdown,
    console,
    ctx_ring,
    escape,
    make_card,
    render_answer_card,
)
from .interact import _arrow_mode, sanitize_input, select_with_arrows
from .render_events import render_event
from .thinking import _usage_line

if TYPE_CHECKING:
    from ..commands import ReplContext


def flush_text_segment(ctx: ReplContext) -> None:
    """当前段正文缓冲落账（v1.1 反馈 F4 §4.2）：非空 → 会话缓冲记为正文段条目；

    空段仅清空不落账不计数（S3 口径）。触发点仅两处：①tool_start 到达（中间步
    正文随首个工具调用落账；loop 自然终止序列的 budget/mode_changed 不触发，
    终答不被误落账）；②交互伪事件落账前（保序，§4.2 触发点 2）。
    落账条目经 live_hooks["step"] 计入折叠行 N（§4.3：N 含正文段落账条目；
    钩子未接线时为 no-op，降级/管道无折叠行不受影响）。
    """
    if ctx.text_segment:
        ctx.session_events.append(("text_segment", {"text": "".join(ctx.text_segment)}))
        ctx.live_hooks.get("step", lambda: None)()
    ctx.text_segment.clear()


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


def thinking_enter(ctx: ReplContext) -> None:
    """pipeline 间隙区间段（如子评审）干净进入思考区（R5）：独立计数不跨段累积。"""
    thinking = getattr(ctx, "thinking", None)
    if thinking is not None:
        thinking.start_turn()
        thinking.start()


def thinking_exit(ctx: ReplContext) -> None:
    """pipeline 间隙区间段结束：收缩为一行摘要（R5；区间段无轮内用量，不附 token 段）。"""
    thinking = getattr(ctx, "thinking", None)
    if thinking is not None:
        thinking.close({})


async def run_managed_turn(ctx: ReplContext, message: str, label: str = "") -> str:
    """Spec pipeline 任务轮壳（v1.1-M5 验收反馈 R1/R3）：复刻 repl 任务轮同款时序。

    pipeline 直调 ctx.loop.run 会绕过 repl 轮壳：思考区计数跨轮累积不收缩、
    正文逐字直打不走 🕊 md 卡片。本壳层提供与 repl 一致的：
    begin_turn 轮级重置 → thinking start_turn/start → loop.run → 轮末收缩摘要行 +
    终答缓冲一次性 md 卡片 + 用量行。异常向上抛（由 pipeline 任务级兜底接住）。
    """
    from ..commands import begin_turn

    begin_turn(ctx)
    thinking = ctx.thinking
    if thinking is not None:
        thinking.start_turn()
        thinking.start()
    ctx.stream_state["printed"] = False
    ctx.turn_active = True
    ctx.turn_checkpoint_seq = None
    answer = ""
    turn_ok = False
    try:
        answer = await ctx.loop.run(message)
        turn_ok = True
        return answer
    finally:
        ctx.turn_active = False
        ctx.turn_checkpoint_seq = None
        usage_acc = ctx.session_usage
        usage_acc["prompt"] += ctx.turn_usage.get("prompt") or 0
        usage_acc["completion"] += ctx.turn_usage.get("completion") or 0
        if ctx.session_index is not None:
            ctx.session_index.touch(
                ctx.history.session_id,
                ctx.workspace,
                auto_name=derive_name(label or message),
                message_count=len(ctx.history.messages),
                token_used=usage_acc["prompt"] + usage_acc["completion"],
            )
        # close 后 was_active 已复位（R5）：先取判据再收缩（终答呈现路径）
        was_active = thinking.was_active if thinking is not None else False
        if thinking is not None:
            thinking.close(ctx.turn_usage)
        if turn_ok:
            body = "".join(ctx.text_segment).strip()
            if body:
                if was_active:
                    ctx.session_events.append(("text_segment", {"text": body}))
                render_answer_card(body)  # 终答统一 md 卡片（同 repl 轮末，R3）
        else:
            flush_text_segment(ctx)  # 异常轮：正文段落账供 /expand，不呈现
        ctx.text_segment.clear()
        usage_text = _usage_line(ctx.turn_usage)
        if usage_text:
            console.print(f"  [glaucous.muted]{usage_text}[/]")


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
            if ctx.active_agent != "主 agent":
                # v1.1-M2（FR-62，概设 §8.3）：子 agent 归属标注卡首行
                table.add_row(
                    "归属",
                    f"[glaucous.sub]🕊 子 agent（任务：{escape(ctx.active_task[:40])}）[/]",
                )
            # 问题正文走 Markdown（markdown.* 主题色板；方括号天然安全，无需 escape）
            if question.strip():
                table.add_row(Markdown(question))
            for i, option in enumerate(options, 1):
                table.add_row(f"[glaucous.title][{i}] {escape(option)}[/]")
            console.print(table)
            if len(options) >= 2 and _arrow_mode():
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
            flush_text_segment(ctx)  # §4.2 触发点 2：伪事件前保序落账正文段
            ctx.session_events.append(("ask", {"summary": f"提问「{question[:40]}」→ 回答：{result or '（未响应）'}"}))
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
        # 附加项 B：EOF/Ctrl+C 视为理由「用户取消」继续拒绝（不再落入本轮失败兑底）
        try:
            return sanitize_input(console.input("  [glaucous.sub]拒绝理由（可留空）: [/]")).strip() or None
        except (EOFError, KeyboardInterrupt):
            console.print()
            return "用户取消"

    def _reject_with_rollback(reason: str | None) -> ApprovalDecision:
        """FR-43「拒绝并回退」（v1.1-M4，spec §3.5）：立即回退本轮入口 checkpoint

        （只回文件不动上下文）；回退失败（GitError/checkpoint 丢失）降级为普通
        拒绝并提示（S5），不击穿本轮。
        """
        store = ctx.checkpoint_store
        cp = store.get(ctx.turn_checkpoint_seq) if store is not None and ctx.turn_checkpoint_seq is not None else None
        if cp is None:
            console.print("[glaucous.error]  回退失败，已按普通拒绝处理：本轮入口 checkpoint 不可用。[/]")
            return ApprovalDecision(choice="reject", reason=reason)
        try:
            store.rollback(cp)
        except Exception as exc:  # noqa: BLE001 —— 回退失败降级（spec S5）
            console.print(f"[glaucous.error]  回退失败，已按普通拒绝处理：{escape(str(exc))}[/]")
            return ApprovalDecision(choice="reject", reason=reason)
        return ApprovalDecision(choice="reject_rollback", reason=reason)

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
            if ctx.active_agent != "主 agent":
                # v1.1-M2（FR-62，概设 §8.3）：子 agent 归属标注卡首行
                table.add_row(
                    "归属",
                    f"[glaucous.sub]🕊 子 agent（任务：{escape(ctx.active_task[:40])}）[/]",
                )
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
            # v1.1-M4（FR-43）：「拒绝并回退」第四选项——仅主 agent 且本轮入口
            # checkpoint 已就位时提供（子 agent/非 Git/创建失败退化为三选项现状）
            rollback_ready = (
                ctx.active_agent == "主 agent" and ctx.turn_checkpoint_seq is not None
            )
            decision: ApprovalDecision | None = None
            if _arrow_mode():
                # 选项集对齐 ApprovalDecision.choice（概设 §5.3、FR-11）；
                # DANGEROUS 呈现不分列（r2-S3），批量豁免安全性由 gate 守卫兜底
                options = ["同意", "同意同类型", "拒绝"] + (["拒绝并回退"] if rollback_ready else [])
                idx = select_with_arrows("请选择：", options)
                if idx is None:
                    decision = ApprovalDecision(choice="reject", reason="用户取消")
                elif idx == 0:
                    decision = ApprovalDecision(choice="approve")
                elif idx == 1:
                    decision = ApprovalDecision(choice="approve_type")
                elif idx == 2:
                    decision = ApprovalDecision(choice="reject", reason=_reject_reason())
                else:
                    decision = _reject_with_rollback(_reject_reason())
            else:
                while decision is None:
                    try:
                        if dangerous:
                            raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[/]" + ("\\[d] 拒绝并回退(附理由)  " if rollback_ready else "") + "\\[c] 拒绝(附理由): [/]")).strip()
                        else:
                            raw = sanitize_input(console.input("  [glaucous.sub]\\[a] 同意  \\[b] 同意同类型  \\[/]" + ("\\[d] 拒绝并回退(附理由)  " if rollback_ready else "") + "\\[c] 拒绝(附理由): [/]")).strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print()
                        decision = ApprovalDecision(choice="reject", reason="用户中断审批")
                        break
                    if raw in ("a", "A", "y", "Y"):
                        decision = ApprovalDecision(choice="approve")
                    elif not dangerous and raw in ("b", "B"):
                        decision = ApprovalDecision(choice="approve_type")
                    elif rollback_ready and raw in ("d", "D"):
                        decision = _reject_with_rollback(_reject_reason())
                    elif raw in ("c", "C", "n", "N"):
                        decision = ApprovalDecision(choice="reject", reason=_reject_reason())
                    else:
                        console.print("[glaucous.error]  无效输入，请重试。[/]")
            flush_text_segment(ctx)  # §4.2 触发点 2：伪事件前保序落账正文段
            ctx.session_events.append(("decision", {
                "summary": f"审批 {action.kind} {action.target} → {decision.choice}",
            }))
            ctx.live_hooks.get("step", lambda: None)()  # N 口径：交互伪事件计入思考步数（§3.1）
            return decision
        finally:
            ctx.live_hooks["resume"]()

    return decide


def make_on_event(ctx: ReplContext, ws, thinking=None):
    """loop 事件回调：正文缓冲 + 动态区滚动 + 会话缓冲（v1.1 反馈 F4 重构）。

    text 增量：折叠激活时累积进当前段正文缓冲（ctx.text_segment）并经 add_text
    进动态区滚动（允许临时泄露，轮末收缩折叠）；降级/管道维持逐字直接打印、不缓冲。
    非 text 事件照常落账会话缓冲（/expand 全会话口径）；tool_start 到达先触发正文段
    flush（§4.2 触发点 1）；diagnostic 必达豁免：不进动态区、即时直接打印（终止
    诊断契约），照常落账。tool_end md 卡片已删除（决策记录②），一律走思考区摘要。
    """

    # 子正文摘要行去重状态（r3-B1：必须活在 make_on_event 闭包层——
    # 声明在 on_event 体内则每次事件重建空列表，去重永不生效）
    child_note: list[str] = []

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            ctx.stream_state["printed"] = True
            if thinking is not None and thinking.active:
                ctx.text_segment.append(payload["text"])
                thinking.add_text(payload["text"])
            else:
                render_event(event, payload, ctx.state)  # 降级/管道：逐字直接打印
            return
        if event == "diagnostic":
            # B4 修复：终止诊断必达——擦除动态区让位后直打可见（终止诊断契约），照常落账；
            # 计入 N 但不占动态区行（§4.3：N 含 diagnostic）
            ctx.session_events.append((event, payload))
            if thinking is not None and thinking.was_active:
                thinking.pause()  # 擦除动态区，诊断行打在原位（不再与重绘交叉）
                render_event(event, payload, ctx.state)
                thinking.resume()
            else:
                render_event(event, payload, ctx.state)
            if thinking is not None:
                thinking.note_step()
            return
        if event == "note":
            # v1.1-M4（B3/S2）：checkpoint 创建失败的一次性告警（store.take_warning）
            ctx.session_events.append((event, payload))
            if thinking is not None and thinking.active:
                thinking.add(event, payload)
            else:
                console.print(f"[glaucous.muted]  ⚠ {escape(str(payload.get('message', '')))}[/]")
            return
        if event == "sub_event" and payload.get("event") == "text":
            # v1.1-M2（spec §5.2，r1-B3）：子正文增量不流式直出，仅折叠摘要——
            # 折叠区经 thinking.add 单行滚动；降级/管道直打一行 dim。
            # v1.1 修订（用户决策 2026-08-30）：text 增量无回看价值（子正文全文
            # 不回传、增量不拼接），落账同步去重——每 agent 只落一条，/expand 不刷屏
            agent = str(payload.get("agent_id", ""))
            if not child_note or child_note[0] != agent:
                child_note.clear()
                child_note.append(agent)
                ctx.session_events.append((event, payload))
                if thinking is not None and thinking.active:
                    thinking.add(event, payload)
                else:
                    console.print(
                        f"[glaucous.dim]  {escape(f'[{agent}]')} 正文生成中…[/]"
                    )
            return
        if event == "budget":
            ctx.last_budget = payload
        if event == "tool_start":
            flush_text_segment(ctx)  # §4.2 触发点 1：中间步正文随首个工具调用落账
        ctx.session_events.append((event, payload))
        if thinking is not None and thinking.active:
            thinking.add(event, payload)
            return
        render_event(event, payload, ctx.state)

    return on_event
