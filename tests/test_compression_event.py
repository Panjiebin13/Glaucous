"""压缩意象事件单测（3.3i2 追加）：L1 裁剪 / L2 压缩成功 / L2 压缩失败的事件发射。

覆盖：_enforce_budget 在 L1/L2 压缩后发 compressed 事件（stage/ok/used/limit/percent，
payload 与 budget 事件同源 build_report）；L2 失败降级加深 L1 时事件 ok=False。
阈值与压缩参数从 budget/compactor 导入，测试不复制魔法数。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from glaucous.agent.loop import AgentLoop
from glaucous.agent.state import SessionState
from glaucous.context.budget import CRITICAL_RATIO, WARN_RATIO, build_report
from glaucous.context.history import History
from glaucous.llm.client import AssistantMessage
from glaucous.tools.base import ToolRegistry

# 单条消息字符数（中文按 /1.5 估算，一轮约 1333 token）+ 预算上限（20k，方便塞满）
_MSG_CHARS = 1000
_LIMIT = 20_000


class FakeLLM:
    """压缩调用用的假 LLM：chat 返回 .text 或抛异常，分别模拟 L2 成败。"""

    def __init__(self, *, text: str | None = None, raise_error: bool = False):
        self._text = text
        self._raise_error = raise_error

    async def chat(self, messages, tools=None, on_text=None):
        if self._raise_error:
            raise RuntimeError("compress failed")
        return SimpleNamespace(text=self._text)


def _push_round(history: History) -> None:
    """推一轮 user+assistant 对话（无工具调用；L1 只裁 tool 轮，此处保持不裁）。"""
    history.push_user("海" * _MSG_CHARS)
    history.push_assistant(AssistantMessage(text="回应：" + "海" * 20))


def _fill_to(history: History, target: float, *, limit: int = _LIMIT) -> None:
    """循环推对话轮直到占用占比 ≥ target。"""
    while build_report(history.view(), limit).percent < target:
        _push_round(history)


def _make_loop(llm: FakeLLM, history: History, events: list) -> AgentLoop:
    return AgentLoop(
        llm,
        ToolRegistry(),
        history,
        SessionState(),
        on_event=lambda e, p: events.append((e, p)),
        context_limit=_LIMIT,
    )


def _last_compressed(events: list) -> dict:
    return [p for e, p in events if e == "compressed"][-1]


async def test_l1_compressed_event(tmp_path: Path) -> None:
    """>70% 档：L1 裁剪后发 compressed 事件（stage=L1），未越过 critical 不触发 L2。"""
    history = History.create("", tmp_path)
    _fill_to(history, WARN_RATIO)
    # 防御：若推过头越过 85%，退回消息（user+assistant 成对）
    while build_report(history.view(), _LIMIT).percent > CRITICAL_RATIO:
        history.messages.pop()
        history.messages.pop()

    events: list = []
    loop = _make_loop(FakeLLM(), history, events)
    result = await loop._enforce_budget()

    assert result is None  # 未耗尽，继续本轮
    assert [e for e, _ in events] == ["compressed"]  # 只发压缩事件
    payload = _last_compressed(events)
    assert payload["stage"] == "L1"
    assert payload["ok"] is True
    assert 0 < payload["used"] < payload["limit"]
    assert payload["percent"] == round(payload["used"] / payload["limit"], 4)


async def test_l2_success_compressed_event(tmp_path: Path) -> None:
    """>85% 档：L1 裁剪无效（无 tool 轮）时追加 L2 压缩，事件 stage=L2 ok=True。"""
    history = History.create("", tmp_path)
    _fill_to(history, CRITICAL_RATIO + 0.01)

    events: list = []
    loop = _make_loop(FakeLLM(text="压缩摘要：已完成主要工作"), history, events)
    result = await loop._enforce_budget()

    assert result is None
    payload = _last_compressed(events)
    assert payload["stage"] == "L2"
    assert payload["ok"] is True
    # 压缩成功：早期历史被合成摘要替换，占用明显回落
    assert payload["percent"] < CRITICAL_RATIO


async def test_l2_failure_compressed_event(tmp_path: Path) -> None:
    """>85% 档 L2 失败：事件 stage=L2 ok=False；一次失败未达上限，继续本轮不终止。"""
    history = History.create("", tmp_path)
    _fill_to(history, CRITICAL_RATIO + 0.01)

    events: list = []
    loop = _make_loop(FakeLLM(raise_error=True), history, events)
    result = await loop._enforce_budget()

    assert result is None
    payload = _last_compressed(events)
    assert payload["stage"] == "L2"
    assert payload["ok"] is False
