#!/usr/bin/env python3
"""上下文压缩管线验证脚本（M2 压缩契约，纯内存 + FakeLLM，无需 API key）。

验证点（对应 .glaucous/plans/20260829-154629-1db4.md 方案）：
1. 预算三档：build_report 对 low / warn(>70%) / critical(>85%) 判定正确；
2. L1 裁剪：旧 tool 轮正文 → 一行 _meta 摘要、最近 2 轮豁免、_trimmed 幂等；
3. 方案锚保留：submit_plan 决策回喂的 _anchor 条目在 L1 中保留原文不重写；
4. L2 成功：早期历史 → 合成摘要消息，占用回落，锚段拼接（路径 + 目标行）；
5. L2 失败降级：FakeLLM 抛异常 → 返回 False、不阻断、历史不被改动；
6. _extract_goal：从方案文本提取「目标」行。

运行：python scripts/verify_compression.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from glaucous.context.budget import CRITICAL_RATIO, WARN_RATIO, build_report
from glaucous.context.compactor import (
    SUMMARY_PREFIX,
    _extract_goal,
    compact_history,
    trim_history,
)
from glaucous.context.history import History
from glaucous.llm.client import AssistantMessage, ToolCall
from glaucous.tools.base import ToolResult

_PLAN_FILE = "20260830-000000-plan.md"


class FakeLLM:
    """压缩调用用的假 LLM：chat 返回 .text 或抛异常，分别模拟 L2 成败。"""

    def __init__(self, *, text: str | None = None, raise_error: bool = False):
        self._text = text
        self._raise_error = raise_error

    async def chat(self, messages, tools=None, on_text=None):
        if self._raise_error:
            raise RuntimeError("compress failed")
        return SimpleNamespace(text=self._text)


# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""))


def section(title: str) -> None:
    print(f"\n== {title} ==")


def push_tool_round(history: History, name: str, content: str, idx: int) -> None:
    """推一轮 assistant(tool_calls) + tool 结果（工具调用配对的入史前提）。"""
    call = ToolCall(id=f"call-{idx}", name=name, arguments='{"path": "a.py"}')
    history.push_assistant(AssistantMessage(text=None, tool_calls=[call]))
    result = ToolResult(
        ok=True,
        content=content,
        metadata={
            "tool": name,
            "args_brief": "a.py",
            "ok": True,
            "duration_ms": 120,
            "lines": 24,
        },
    )
    history.push_tool(call, result)


def push_plain_round(history: History, idx: int) -> None:
    """推一轮 user + assistant（无工具调用，用于 L2 早期历史）。"""
    history.push_user(f"第{idx}轮用户问题：" + "海" * 300)
    history.push_assistant(AssistantMessage(text=f"第{idx}轮回复：" + "海" * 30))


def _write_plan(plans_dir: Path) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / _PLAN_FILE).write_text(
        "## 目标：验证上下文压缩管线\n## 步骤\n1. 构造长历史\n2. 触发 L1/L2\n",
        encoding="utf-8",
    )


def verify_budget() -> None:
    section("1. 预算三档（build_report）")
    limit = 20_000
    low = build_report([{"role": "user", "content": "海" * 1000}], limit)
    warn = build_report([{"role": "user", "content": "海" * 22_000}], limit)
    critical = build_report([{"role": "user", "content": "海" * 30_000}], limit)

    check("low：占用低于 70% 判为 low", low.level == "low" and low.percent <= WARN_RATIO,
          f"used={low.used} percent={low.percent:.1%}")
    check("warn：占用落入 (70%, 85%] 判为 warn",
          warn.level == "warn" and WARN_RATIO < warn.percent <= CRITICAL_RATIO,
          f"used={warn.used} percent={warn.percent:.1%}")
    check("critical：占用高于 85% 判为 critical",
          critical.level == "critical" and critical.percent > CRITICAL_RATIO,
          f"used={critical.used} percent={critical.percent:.1%}")


def verify_l1(tmp: Path) -> None:
    section("2. L1 裁剪（trim_history）")
    history = History.create("", tmp)
    # 5 个普通 tool 轮：前 3 旧（可裁），后 2 新（豁免）；第 4 轮换成 submit_plan 锚轮
    for i in range(3):
        push_tool_round(history, "read_file", f"旧轮内容 {i}：" + "x" * 200, idx=i)
    # 锚轮：submit_plan 决策回喂（push_tool 会标记 _anchor）
    call = ToolCall(id="call-anchor", name="submit_plan", arguments='{"plan": "..."}')
    history.push_assistant(AssistantMessage(text=None, tool_calls=[call]))
    history.push_tool(
        call,
        ToolResult(ok=True, content="用户已确认方案（锚行原文应被 L1 保留）", metadata={}),
    )
    for i in range(3, 5):
        push_tool_round(history, "grep", f"新轮内容 {i}：" + "y" * 200, idx=i)

    before = [m for m in history.messages if m.get("role") == "tool"]
    before_texts = {m["tool_call_id"]: m.get("content") for m in before}
    used_before = build_report(history.view(), 128_000).used

    trimmed = trim_history(history.messages)

    after = {m["tool_call_id"]: m for m in history.messages if m.get("role") == "tool"}
    check("旧轮 tool 正文被替换为一行摘要", trimmed >= 3,
          f"裁剪 {trimmed} 条（3 旧轮 + 锚轮标记）")
    old = after["call-0"]
    check("摘要格式为 ⎿ 工具 · 参数 · 成功 · 耗时 · 行数",
          old.get("content", "").startswith("⎿ read_file")
          and "成功" in old.get("content", "") and "120ms" in old.get("content", "")
          and old.get("_trimmed") is True,
          f"content={old.get('content')!r}")
    check("最近 2 轮豁免（正文保留原文）",
          after["call-3"].get("content") == before_texts["call-3"]
          and after["call-4"].get("content") == before_texts["call-4"])
    anchor = after["call-anchor"]
    check("方案锚条目保留原文不重写",
          anchor.get("content") == "用户已确认方案（锚行原文应被 L1 保留）"
          and anchor.get("_trimmed") is True and anchor.get("_anchor") is True)
    check("_trimmed 幂等（二次调用 0 裁剪）", trim_history(history.messages) == 0)

    used_after = build_report(history.view(), 128_000).used
    print(f"  · 占用对比：{used_before} → {used_after} tokens")


async def verify_l2(tmp: Path) -> None:
    section("3. L2 压缩（compact_history）")
    plans_dir = Path(tmp) / "plans"
    _write_plan(plans_dir)

    # -- 3a. 成功路径 --
    history = History.create("", Path(tmp) / "ok")
    for i in range(4):  # 4 个 assistant 轮 > keep_recent=2，早期段非空
        push_plain_round(history, i)
    messages = history.messages
    used_before = build_report(history.view(), 128_000).used
    n_before = len(messages)

    ok = await compact_history(messages, FakeLLM(text="压缩摘要：已完成主要工作"), plans_dir=plans_dir)

    check("L2 成功返回 True", ok is True)
    synthetic = messages[0]
    check("早期历史被替换为单条合成摘要消息",
          len(messages) < n_before and synthetic.get("role") == "user"
          and synthetic.get("content", "").startswith(SUMMARY_PREFIX),
          f"消息数 {n_before} → {len(messages)}")
    anchor_text = synthetic.get("content", "")
    check("锚段拼接（含 read_plan 路径）", f"方案锚：.glaucous/plans/{_PLAN_FILE}" in anchor_text)
    check("锚段拼接（含方案目标行）", "方案目标：验证上下文压缩管线" in anchor_text)

    used_after = build_report(history.view(), 128_000).used
    check("压缩后占用明显回落", used_after < used_before,
          f"{used_before} → {used_after} tokens")
    print(f"  · 占用对比：{used_before} → {used_after} tokens，消息 {n_before} → {len(messages)} 条")

    # -- 3b. 失败路径（不阻断） --
    history2 = History.create("", Path(tmp) / "fail")
    for i in range(4):
        push_plain_round(history2, i)
    messages2 = history2.messages
    snapshot = [dict(m) for m in messages2]

    ok2 = await compact_history(messages2, FakeLLM(raise_error=True), plans_dir=None)

    check("L2 失败返回 False", ok2 is False)
    check("L2 失败不改动历史（调用方降级 L1 加深）",
          [dict(m) for m in messages2] == snapshot)


def verify_goal_extract() -> None:
    section("4. 方案目标行提取（_extract_goal）")
    check("容忍 ## / ** 标记前缀",
          _extract_goal("## 目标：修复登录 bug\n## 步骤\n1. x") == "修复登录 bug")
    check("无目标行返回空串", _extract_goal("## 步骤\n1. x") == "")


async def main() -> int:
    print("Glaucous 上下文压缩管线验证")
    print("=" * 40)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        verify_budget()
        verify_l1(tmp)
        await verify_l2(tmp)
    verify_goal_extract()

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print("\n" + "=" * 40)
    print(f"汇总：PASS {passed} / FAIL {failed}（共 {len(_results)} 项）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
