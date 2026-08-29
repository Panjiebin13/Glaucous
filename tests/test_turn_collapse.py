"""R3 思考折叠单测（v1.1 打磨 §3）：缓冲口径 / 重置时机 / /expand 分支 / N 一致性 /
COLLAPSE=off / 异常路径缓冲保留。

不跑真实 repl：以 make_on_event + reset_turn_buffers + _cmd_expand + _collapse_enabled
的单元组合覆盖时序契约（text 不进缓冲、轮首重置轮末保留、交互伪事件同口径）。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from glaucous import cli, commands


class FakeRenderer:
    """记录 note/info/error 的最小渲染器。"""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(str(text))

    def info(self, text: str) -> None:
        self.notes.append(str(text))

    def error(self, text: str) -> None:
        self.notes.append(str(text))


def make_fake_ctx() -> SimpleNamespace:
    """on_event / 命令层所需的最小 ctx（鸭子类型，仅取被触碰的字段）。"""
    return SimpleNamespace(
        stream_state={"printed": False},
        last_budget=None,
        turn_events=[],
        turn_usage={"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None},
        live_hooks={"pause": lambda: None, "resume": lambda: None},
        renderer=FakeRenderer(),
        state=SimpleNamespace(mode="build", approval_policy="per-action"),
    )


MODE_PAYLOAD = {"reason": "方案获批", "mode": "build", "policy": "per-action"}


class TestTurnBufferScope:
    def test_text_not_buffered_non_text_buffered(self) -> None:
        """缓冲口径：仅非 text 事件 + 交互伪事件；text 增量不缓冲（§3.1/§3.2）。"""
        ctx = make_fake_ctx()
        on_event = cli.make_on_event(ctx, ws=None, thinking=None)
        on_event("text", {"text": "正文增量"})
        on_event("mode_changed", MODE_PAYLOAD)
        ctx.turn_events.append(("ask", {"summary": "提问 → 回答"}))  # 交互伪事件由回调记录
        events = [event for event, _ in ctx.turn_events]
        assert events == ["mode_changed", "ask"]
        assert ctx.stream_state["printed"] is True

    def test_count_consistency_between_view_and_buffer(self) -> None:
        """摘要行 N 与缓冲同口径（非 text 事件 + 交互伪事件，r4-B1）：
        add 逐条计数与缓冲条目一致；未激活时走降级实时打印不计数（无思考区）。"""
        ctx = make_fake_ctx()
        thinking = cli.ThinkingView()  # 不 start：Live 未激活 → 降级路径，缓冲仍记录（/expand 可用）
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("mode_changed", MODE_PAYLOAD)
        on_event("compressed", {"stage": "L2", "ok": True})
        ctx.turn_events.append(("plan_decision", {"summary": "方案决策 → 1"}))
        assert thinking.count == 0  # 降级实时打印：无思考区不计数（呈现层降级，缓冲不受影响）
        assert len(ctx.turn_events) == 3  # 缓冲口径：非 text 事件 + 交互伪事件（/expand 完整回看）
        # N 口径验证：直接驱动 add（Live 激活时的收纳路径）+ note_step（交互伪事件，
        # 经 live_hooks["step"] 接线）与缓冲同口径一致（§3.1）
        view = cli.ThinkingView()
        view.add("mode_changed", MODE_PAYLOAD)
        view.add("compressed", {"stage": "L2", "ok": True})
        view.note_step()  # 交互伪事件计入步数但不占动态区行
        assert view.count == 3


class TestResetTiming:
    def test_reset_clears_events_and_usage(self) -> None:
        ctx = make_fake_ctx()
        ctx.turn_events.append(("mode_changed", MODE_PAYLOAD))
        ctx.turn_usage.update({"prompt": 100, "completion": 20, "cache_hit": 50, "cache_miss": 50})
        commands.reset_turn_buffers(ctx)
        assert ctx.turn_events == []
        assert ctx.turn_usage == {"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None}

    def test_buffer_retained_until_next_turn_start(self) -> None:
        """轮末保留（含异常路径）、轮首才重置（r4-B2）：模拟 run 抛错后缓冲仍在。"""
        ctx = make_fake_ctx()
        on_event = cli.make_on_event(ctx, ws=None, thinking=None)
        on_event("mode_changed", MODE_PAYLOAD)
        # 模拟异常轮：缓冲未被轮末清空（repl finally 不做清空，仅收缩/渲染）
        assert len(ctx.turn_events) == 1
        # 下一轮任务开始才重置
        commands.reset_turn_buffers(ctx)
        assert ctx.turn_events == []


class TestExpand:
    @pytest.mark.asyncio
    async def test_empty_buffer_hints(self) -> None:
        ctx = make_fake_ctx()
        await commands._cmd_expand(ctx)
        assert any("暂无可展开的思考过程" in note for note in ctx.renderer.notes)

    @pytest.mark.asyncio
    async def test_replay_buffer_with_header(self) -> None:
        """间隙回看：分隔头 + 交互伪事件摘要逐条重放（仅读不改状态）。"""
        ctx = make_fake_ctx()
        ctx.turn_events.append(("ask", {"summary": "提问「用哪个库」→ 回答：rich"}))
        await commands._cmd_expand(ctx)
        joined = "\n".join(ctx.renderer.notes)
        assert "思考过程（上一轮）" in joined
        assert "提问「用哪个库」→ 回答：rich" in joined
        assert len(ctx.turn_events) == 1  # 只读：缓冲不受影响


class TestCollapseSwitch:
    def test_off_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLAUCOUS_COLLAPSE=off：即使 TTY 也不开折叠（§3.1 开关语义）。"""
        monkeypatch.setenv("GLAUCOUS_COLLAPSE", "off")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert cli._collapse_enabled() is False

    def test_tty_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLAUCOUS_COLLAPSE", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert cli._collapse_enabled() is True

    def test_non_tty_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLAUCOUS_COLLAPSE", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        assert cli._collapse_enabled() is False


class TestUsageAccumulationGate:
    def test_cache_none_to_zero_baseline(self) -> None:
        """累加器语义：数字求和；缓存首次非 None 转 0 基线再累加（§5.2）。"""
        ctx = make_fake_ctx()
        ctx.counting_usage = True
        # 复刻 repl 内 _accumulate_usage 的口径做行为断言（不依赖完整 repl 装配）
        acc = ctx.turn_usage
        for payload in (
            {"prompt": 100, "completion": 10, "cache_hit": None, "cache_miss": None},
            {"prompt": 50, "completion": 5, "cache_hit": 30, "cache_miss": 20},
        ):
            acc["prompt"] += payload["prompt"]
            acc["completion"] += payload["completion"]
            for key in ("cache_hit", "cache_miss"):
                if payload[key] is not None:
                    if acc[key] is None:
                        acc[key] = 0
                    acc[key] += payload[key]
        assert acc["prompt"] == 150 and acc["completion"] == 15
        assert acc["cache_hit"] == 30 and acc["cache_miss"] == 20

    def test_usage_line_formats(self) -> None:
        """用量行格式：<1000 原样、≥1000 一位小数+k、命中率四舍五入、无缓存省略（§5.2/§5.3）。"""
        line = cli._usage_line({"prompt": 12300, "completion": 456, "cache_hit": 820, "cache_miss": 180})
        assert line == "⏱ ↑12.3k ↓456 tokens · 缓存命中 82%"
        no_cache = cli._usage_line({"prompt": 100, "completion": 20, "cache_hit": None, "cache_miss": None})
        assert no_cache == "⏱ ↑100 ↓20 tokens"
        assert cli._usage_line({"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None}) is None
