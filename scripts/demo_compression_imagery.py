#!/usr/bin/env python3
"""压缩意象触发演示（端到端）：走真实 AgentLoop.run() 主循环，复用真实渲染层。

背景：现有单测只直接调 _enforce_budget() 验证事件发射，未走完整主循环、也未
渲染意象。本脚本用 FakeLLM 注入驱动真实 loop.run()，让守卫点真实触发 L1/L2
压缩，并用 cli.render_event（🌊 潮汐意象）+ cli.render_prompt_header（占用条
意象 ctx_ring 三档变色）实时渲染——你在真终端跑即可看到彩色意象。

三个阶段：
A. 占用条意象：ctx_ring 三档（海草绿 ○ / 晚霞橙 ◔◕ / 陶土红 ●）随占用变化；
B. 主循环触发：历史预填到 critical，loop.run() 真实触发 L1→L2，🌊 意象逐条弹出；
C. L2 失败意象：压缩调用抛异常 → 🌊 潮水不退（晚霞橙），不阻断主流程。

运行：python scripts/demo_compression_imagery.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from glaucous.agent.loop import AgentLoop
from glaucous.agent.state import SessionState
from glaucous.cli import render_event, render_prompt_header
from glaucous.context.budget import BudgetReport, build_report, level_of
from glaucous.context.history import History
from glaucous.llm.client import AssistantMessage, ToolCall
from glaucous.permission.workspace import Workspace
from glaucous.tools.base import ToolRegistry, ToolResult
from glaucous.tools.files import ReadFileTool

DEMO_MODEL = "deepseek-v4-flash"
LIMIT = 128_000

# ---------------------------------------------------------------------------
# FakeLLM：同一实例服务「主循环调用」与「压缩调用」（loop 守卫点共用 self._llm）。
# 区分方式：主循环 messages 恒以 role=system 开头（view()），压缩调用只有
# [{"role": "user", ...}] 单条（_build_compress_prompt）。
# ---------------------------------------------------------------------------


class ScriptLLM:
    def __init__(self, *, compress_ok: bool = True, compress_text: str = "压缩摘要：已完成主要工作"):
        self._compress_ok = compress_ok
        self._compress_text = compress_text
        self._main_calls = 0

    async def chat(self, messages, tools=None, on_text=None):
        if messages and messages[0].get("role") == "system":
            # —— 主循环调用：第 1 次发工具调用，第 2 次自然终答 ——
            self._main_calls += 1
            if self._main_calls == 1:
                return AssistantMessage(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id=f"demo-{self._main_calls}",
                            name="read_file",
                            arguments='{"path": "src/glaucous/__init__.py"}',
                        )
                    ],
                )
            return AssistantMessage(text="演示完成：上下文已压缩，任务目标与方案锚保留。")
        # —— 压缩调用 ——
        if not self._compress_ok:
            raise RuntimeError("compress failed (fake)")
        return AssistantMessage(text=self._compress_text)


# ---------------------------------------------------------------------------
# 预填历史
# ---------------------------------------------------------------------------


def push_tool_round(history: History, idx: int) -> None:
    """推一轮真实工具调用：assistant(tool_calls) + tool 结果（配对入史）。"""
    call = ToolCall(id=f"fill-{idx}", name="grep", arguments='{"pattern": "TODO"}')
    history.push_assistant(AssistantMessage(text=None, tool_calls=[call]))
    history.push_tool(
        call,
        ToolResult(
            ok=True,
            content=f"填充轮 {idx}：" + "海" * 400,
            metadata={
                "tool": "grep",
                "args_brief": "TODO",
                "ok": True,
                "duration_ms": 50,
                "lines": 30,
            },
        ),
    )


def push_plain_round(history: History, idx: int) -> None:
    """推一轮纯对话（无工具：L1 裁不动，保证 L2 触发）。"""
    history.push_user(f"第{idx}轮用户问题：" + "海" * 600)
    history.push_assistant(AssistantMessage(text=f"第{idx}轮回复：" + "海" * 120))


def fill_to_critical(history: History, limit: int = LIMIT) -> None:
    """只推纯对话轮（无 tool 结果：L1 裁不动），直到占用 > 86%（critical 档）。

    纯对话轮逼出 L2：贴近真实「长问答」场景——L1 无 tool 可裁，只能靠
    L2 模型压缩降占用。
    """
    i = 0
    while build_report(history.view(), limit).percent <= 0.86:
        push_plain_round(history, i)
        i += 1


# ---------------------------------------------------------------------------
# 阶段 A：占用条意象三档
# ---------------------------------------------------------------------------


def stage_a() -> None:
    print("\n【阶段 A】占用条意象：ctx_ring 圆环三档变色（真终端见色）")
    for ratio in (0.30, 0.75, 0.90):
        report = BudgetReport(
            used=int(LIMIT * ratio),
            limit=LIMIT,
            percent=ratio,
            level=level_of(ratio),
        )
        render_prompt_header(DEMO_MODEL, report)
        note = "海草绿" if ratio <= 0.70 else "晚霞橙" if ratio <= 0.85 else "陶土红"
        print(f"        ↑ {ratio:.0%} 占用（{note}）")
    print("        ↑ 阈值与压缩管线同源：>70% L1 / >85% L2 / ≥100% 终止")


# ---------------------------------------------------------------------------
# 阶段 B：真实主循环触发 L1→L2
# ---------------------------------------------------------------------------


def run_loop(history: History, llm: ScriptLLM, task: str, tmp: Path) -> tuple[list, str]:
    """跑一轮真实主循环：注册真实 read_file 工具 + 渲染事件流，返回（事件列表, 终答）。"""
    events: list[tuple[str, dict]] = []
    registry = ToolRegistry()
    registry.register(ReadFileTool(Workspace(Path.cwd())))
    loop = AgentLoop(
        llm,
        registry,
        history,
        SessionState(),
        on_event=lambda e, p: (events.append((e, p)), render_event(e, p, SessionState())),
        context_limit=LIMIT,
    )
    final = asyncio.run(loop.run(task))
    return events, final


def stage_b(tmp: Path) -> str:
    print("\n【阶段 B】真实主循环触发（历史预填到 critical，loop.run 全链路）")
    history = History.create("", Path(tmp) / "b")
    fill_to_critical(history)
    report0 = build_report(history.view(), LIMIT)
    print(f"  预填后占用：{report0.used} / {report0.limit} tokens（{report0.percent:.1%}，{report0.level}）")

    events, final = run_loop(history, ScriptLLM(), "演示：触发上下文压缩意象", tmp)
    stages = [p["stage"] for e, p in events if e == "compressed"]
    budget_n = sum(1 for e, _ in events if e == "budget")
    print(f"\n  事件序列：{[e for e, _ in events]}")
    print(f"  压缩意象：{stages}")
    ok = "L1" in stages and "L2" in stages and budget_n >= 1 and bool(final)
    print(f"  [{'PASS' if ok else 'FAIL'}] L1+L2 意象均已触发，占用条事件 {budget_n} 次，终答: {final[:20]}…")
    return "PASS" if ok else "FAIL"


# ---------------------------------------------------------------------------
# 阶段 C：L2 失败意象（不阻断）
# ---------------------------------------------------------------------------


def stage_c(tmp: Path) -> str:
    print("\n【阶段 C】L2 失败意象（压缩调用抛异常 → 🌊 潮水不退，不阻断主流程）")
    history = History.create("", Path(tmp) / "c")
    fill_to_critical(history)

    events, final = run_loop(history, ScriptLLM(compress_ok=False), "演示：L2 压缩失败降级", tmp)
    comp = [p for e, p in events if e == "compressed"]
    l2_fail = any(p.get("stage") == "L2" and not p.get("ok") for p in comp)
    print(f"\n  事件序列：{[e for e, _ in events]}")
    print(f"  [{'PASS' if l2_fail else 'FAIL'}] L2 失败意象触发，终答: {final[:24]}…")
    return "PASS" if l2_fail else "FAIL"


def main() -> int:
    print("Glaucous 压缩意象触发演示（端到端 · 真实渲染层）")
    print("=" * 56)
    stage_a()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        r_b = stage_b(tmp)
        r_c = stage_c(tmp)
    print("\n" + "=" * 56)
    print(f"汇总：阶段 B（主循环触发）{r_b} / 阶段 C（失败降级）{r_c}")
    return 0 if r_b == "PASS" and r_c == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
