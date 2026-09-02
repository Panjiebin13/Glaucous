"""loop 事件 → 主题化渲染（自 cli.py 拆出，v1.1 评审重构）。

render_event 是「⏺ 动作行 / ⎿ 结果行」的完整渲染（学 Claude Code 的密度）；
_thinking_line 是同一信息源的紧凑单行形态（思考区滚动 + /expand 回看共用）。
两者必须保持同源：文案/emoji/截断口径改动需同步两处。
"""

from __future__ import annotations

from typing import Any

from ..agent.state import POLICY_PER_ACTION, SessionState
from ..theme import console, ctx_ring, escape

# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要，M3 折叠升级）
RESULT_TAIL_LINES = 3


def render_event(event: str, payload: dict[str, Any], state: SessionState) -> None:
    """loop 事件 → 主题化渲染（⏺ 动作行 / ⎿ 结果行，学 Claude Code 的密度）。"""
    if event == "text":
        # 流式正文：markup/emoji 关闭保证逐字保真（模型输出里的 [...] 不被吞）
        console.print(payload["text"], end="", soft_wrap=True, markup=False, emoji=False)
    elif event == "diagnostic":
        # 终止诊断（步数上限/解析熔断）：loop 显式通知，保证多步轮中必达
        console.print(f"[glaucous.warn]\n  ⎿ {escape(payload['text'])}[/]")
    elif event == "note":
        # v1.1-M4：checkpoint 一次性告警（含 /expand 回放路径）
        console.print(f"[glaucous.muted]  ⚠ {escape(str(payload.get('message', '')))}[/]")
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
    elif event == "budget":
        # 预算评估（与输入区头部圆环同源：theme.ctx_ring 三档变色；载荷 percent 为 0~1 比例）
        ring, level_style = ctx_ring(payload.get("percent", 0.0))
        console.print(
            f"[{level_style}]  {ring} ctx 占用 {round(payload.get('percent', 0.0) * 100)}%"
            f"（{payload.get('used', '?')}/{payload.get('limit', '?')} tokens）[/]"
        )
    elif event == "sub_start":
        # v1.1-M2（概设 §9 意象）：子 agent 派发行
        task = str(payload.get("task", ""))
        console.print(
            f"[glaucous.sub]  🕊 子 agent 出发 · {escape(str(payload.get('agent_id', '')))} "
            f"{escape(task[:60])}[/]"
        )
    elif event == "sub_event":
        # 子 agent 中间过程：两格缩进复用既有紧凑形态（text 不直出，仅报告承担）
        agent_id = str(payload.get("agent_id", ""))
        inner = str(payload.get("event", ""))
        inner_payload = payload.get("payload", {}) or {}
        if inner == "text":
            # /expand 重放呈现：折叠摘要形态（spec §5.2，r1-B3）；
            # [child-N] 字面量需 escape（rich 未知标签会被静默吞，r2-S1）
            console.print(
                f"[glaucous.dim]  {escape(f'[{agent_id}]')} 正文生成中…[/]"
            )
            return
        if inner == "tool_start":
            call = inner_payload["call"]
            brief = _tool_brief(call.arguments)
            console.print(
                f"\n  ⏺ [glaucous.tool]{escape(agent_id)}·{escape(call.name)}[/] "
                f"[glaucous.text]{escape(brief)}[/]"
            )
        elif inner == "tool_end":
            result = inner_payload["result"]
            lines = (result.content or "").splitlines()
            if result.ok:
                summary = " | ".join(lines[-RESULT_TAIL_LINES:]) if lines else "（无输出）"
                if len(lines) > RESULT_TAIL_LINES:
                    summary = f"…共 {len(lines)} 行 | {summary}"
            else:
                summary = f"✘ {result.content}"
            level_style = "glaucous.ok" if result.ok else "glaucous.error"
            console.print(f"[{level_style}]      ⎿ {escape(summary)}[/]")
        else:
            # [child-N] 字面量需 escape（rich 未知标签静默吞，r2-S1）
            console.print(f"[glaucous.dim]    {escape(f'[{agent_id}]')} {escape(_thinking_line(inner, inner_payload))}[/]")
    elif event == "sub_end":
        # 子 agent 完成行：报告首段摘要（海草绿/陶土红按 ok）
        level_style = "glaucous.ok" if payload.get("ok", True) else "glaucous.error"
        console.print(
            f"[{level_style}]  ⎿ 子 agent {escape(str(payload.get('agent_id', '')))} 完成 · "
            f"{escape(str(payload.get('brief', '')))}[/]"
        )
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
    if event == "note":
        # v1.1-M4（S7）：checkpoint 一次性告警在折叠思考区实时可见
        return f"⚠ {payload.get('message', '')}"
    if event == "mode_changed":
        policy = "·每次审批" if payload.get("policy") == POLICY_PER_ACTION else "·自动放行"
        return f"◆ {payload.get('reason', '')}（{payload.get('mode', '')}{policy}）"
    if event == "compressed":
        if payload.get("stage") == "L1":
            return "🌊 涨潮了，归档早期对话"
        return "🌊 涨潮了，压缩上下文" if payload.get("ok") else "🌊 潮水不退，继续精简对话"
    if event == "budget":
        ring, _ = ctx_ring(payload.get("percent", 0.0))  # 圆环取形与 render_event 同源（不硬编码）
        return f"{ring} ctx 占用 {round(payload.get('percent', 0.0) * 100)}%（{payload.get('used', '?')}/{payload.get('limit', '?')} tokens）"
    if event == "sub_start":
        return f"🕊 子 agent 出发 · {payload.get('agent_id', '')} {str(payload.get('task', ''))[:60]}"
    if event == "sub_end":
        mark = "✓" if payload.get("ok", True) else "✘"
        return f"⎿ 子 agent {payload.get('agent_id', '')} 完成 {mark} · {payload.get('brief', '')}"
    if event == "sub_event":
        inner = str(payload.get("event", ""))
        if inner == "text":
            return f"[{payload.get('agent_id', '')}] 正文生成中…"
        line = _thinking_line(inner, payload.get("payload", {}) or {})
        return f"[{payload.get('agent_id', '')}] {line}"
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
