"""思考折叠缓冲时序单测（v1.1 反馈修复批次 F4）。

覆盖 spec §4.2~§4.5 契约：正文段缓冲与落账（tool_start 触发、终答后
budget/mode_changed 不触发、空段不落账、伪事件前保序）、begin_turn 轮级重置
（不动会话缓冲）、/clear 与 /resume 重置会话缓冲、diagnostic 必达直打并计入 N、
/expand 全会话重放、异常路径正文段落账、GLAUCOUS_COLLAPSE=off / 非 TTY 降级。

不跑真实 repl：以 make_on_event + begin_turn + flush_text_segment + _cmd_expand
+ _collapse_enabled 的单元组合覆盖时序契约。
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
        self.last_budget = None

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
        session_events=[],
        text_segment=[],
        turn_usage={"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None},
        live_hooks={"pause": lambda: None, "resume": lambda: None},
        renderer=FakeRenderer(),
        state=SimpleNamespace(mode="build", approval_policy="per-action"),
    )


def make_active_thinking() -> cli.ThinkingView:
    """折叠激活态（v1.1 修订：自管重绘协议无 Live 可注入，was_active 即判据）。"""
    view = cli.ThinkingView()
    view.start()
    return view


def wire_step(ctx: SimpleNamespace, thinking: cli.ThinkingView) -> None:
    """接线 N 口径（复刻 repl：折叠启用时 live_hooks["step"] = note_step）。"""
    ctx.live_hooks["step"] = thinking.note_step


MODE_PAYLOAD = {"reason": "方案获批", "mode": "build", "policy": "per-action"}


class TestTextSegmentFlush:
    """正文段缓冲与落账时序（spec §4.2，B1 修复核心）。"""

    def test_degraded_text_prints_directly_not_buffered(self) -> None:
        """降级/管道（thinking=None）：text 逐字直接打印，不进正文段缓冲。"""
        ctx = make_fake_ctx()
        on_event = cli.make_on_event(ctx, ws=None, thinking=None)
        on_event("text", {"text": "正文增量"})
        assert ctx.stream_state["printed"] is True
        assert ctx.text_segment == []
        assert ctx.session_events == []

    def test_active_text_buffered_and_flushed_on_tool_start(self) -> None:
        """折叠激活：text 累积进正文段缓冲；tool_start 到达先落账（触发点 1）。

        缓冲时序：text_segment 条目先于 tool_start 条目（/expand 时序保序）；
        正文段落账条目计入 N（经 live_hooks["step"]，§4.3）。
        """
        ctx = make_fake_ctx()
        thinking = make_active_thinking()
        wire_step(ctx, thinking)
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("text", {"text": "中间步"})
        on_event("text", {"text": "正文"})
        assert "".join(ctx.text_segment) == "中间步正文"
        on_event("tool_start", {"call": SimpleNamespace(name="grep", arguments="pattern")})
        events = [event for event, _ in ctx.session_events]
        assert events == ["text_segment", "tool_start"]
        assert ctx.text_segment == []
        # N 口径：正文段（flush step +1）+ tool_start（add +1）
        assert thinking.count == 2

    def test_final_answer_not_flushed_by_budget(self) -> None:
        """终答不被误落账（B1）：自然终止序列 budget/mode_changed 不是触发点。

        loop 事实（agent/loop.py 自然终止）：终答正文 → budget → mode_changed；
        终答留在正文段缓冲，由 repl 轮末呈现（§4.4 步骤 3）。
        """
        ctx = make_fake_ctx()
        thinking = make_active_thinking()
        wire_step(ctx, thinking)
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        on_event("text", {"text": "最终回答"})
        on_event("budget", {"percent": 0.1, "level": "low"})
        on_event("mode_changed", MODE_PAYLOAD)
        events = [event for event, _ in ctx.session_events]
        assert "text_segment" not in events
        assert "".join(ctx.text_segment) == "最终回答"

    def test_empty_segment_not_recorded(self) -> None:
        """空正文段不落账不计数（S3）：纯工具步（无正文）不产生 text_segment 条目。"""
        ctx = make_fake_ctx()
        thinking = make_active_thinking()
        wire_step(ctx, thinking)
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        events = [event for event, _ in ctx.session_events]
        assert events == ["tool_start"]
        assert thinking.count == 1  # 仅 tool_start 自身，无 flush 计数

    def test_abnormal_turn_flush_records_segment(self) -> None:
        """异常轮（B3）：finally 中 flush 落账正文段后清空（供 /expand，不呈现）。"""
        ctx = make_fake_ctx()
        thinking = make_active_thinking()
        wire_step(ctx, thinking)
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("text", {"text": "被中断的正文"})
        cli.flush_text_segment(ctx)  # repl finally 异常路径（§4.4 步骤 5）
        events = [event for event, _ in ctx.session_events]
        assert events == ["text_segment"]
        assert ctx.text_segment == []

    def test_flush_unit_semantics(self) -> None:
        """flush_text_segment 单元语义：非空落账+清空；空仅清空。"""
        ctx = make_fake_ctx()
        cli.flush_text_segment(ctx)
        assert ctx.session_events == []
        ctx.text_segment.append("内容")
        cli.flush_text_segment(ctx)
        assert ctx.session_events == [("text_segment", {"text": "内容"})]
        assert ctx.text_segment == []


class TestDiagnosticBypass:
    """diagnostic 必达豁免（B4）：直打不进动态区，落账并计入 N。"""

    def test_diagnostic_prints_records_counts_not_windowed(self) -> None:
        ctx = make_fake_ctx()
        thinking = make_active_thinking()
        wire_step(ctx, thinking)
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        on_event("diagnostic", {"text": "已达步数上限"})
        # 落账（/expand 可回看）
        assert ("diagnostic", {"text": "已达步数上限"}) in ctx.session_events
        # 计入 N（§4.3 含 diagnostic）但不占动态区行
        assert thinking.count == 2
        assert len(thinking._lines) == 1  # 仅 tool_start 摘要行


class TestBufferScope:
    def test_non_text_buffered_degraded(self) -> None:
        """降级口径：非 text 事件 + 交互伪事件入会话缓冲（§4.5：管道 /expand 可用）。"""
        ctx = make_fake_ctx()
        on_event = cli.make_on_event(ctx, ws=None, thinking=None)
        on_event("mode_changed", MODE_PAYLOAD)
        ctx.session_events.append(("ask", {"summary": "提问 → 回答"}))  # 交互伪事件由回调记录
        events = [event for event, _ in ctx.session_events]
        assert events == ["mode_changed", "ask"]

    def test_count_consistency_between_view_and_buffer(self) -> None:
        """摘要行 N 与缓冲同口径（§4.3）：降级不计数不占行；active 逐条收纳一致。"""
        ctx = make_fake_ctx()
        thinking = cli.ThinkingView()  # 未启动 → 降级路径，缓冲仍记录
        on_event = cli.make_on_event(ctx, ws=None, thinking=thinking)
        on_event("mode_changed", MODE_PAYLOAD)
        on_event("compressed", {"stage": "L2", "ok": True})
        ctx.session_events.append(("plan_decision", {"summary": "方案决策 → 1"}))
        assert thinking.count == 0  # 降级：无思考区不计数（diagnostic 除外）
        assert len(ctx.session_events) == 3
        view = make_active_thinking()
        view.add("mode_changed", MODE_PAYLOAD)
        view.add("compressed", {"stage": "L2", "ok": True})
        view.note_step()  # 交互伪事件计数不占行
        assert view.count == 3


class TestBeginTurn:
    """begin_turn 轮级重置（B2）：清轮级状态，不动会话缓冲。"""

    def test_clears_turn_level_keeps_session_buffer(self) -> None:
        ctx = make_fake_ctx()
        ctx.session_events.append(("mode_changed", MODE_PAYLOAD))
        ctx.session_events.append(("text_segment", {"text": "上轮中间步正文"}))
        ctx.text_segment.append("当前段残留")
        ctx.turn_usage.update({"prompt": 100, "completion": 20, "cache_hit": 50, "cache_miss": 50})
        commands.begin_turn(ctx)
        # 会话缓冲跨轮保留（/expand 回看全会话，仅 /clear、/resume 清空）
        assert len(ctx.session_events) == 2
        assert ctx.text_segment == []
        assert ctx.turn_usage == {"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None}

    def test_multi_turn_accumulation(self) -> None:
        """多轮累积：轮 1 落账 → begin_turn → 轮 2 落账，全会话缓冲线性增长。"""
        ctx = make_fake_ctx()
        on_event = cli.make_on_event(ctx, ws=None, thinking=None)
        on_event("mode_changed", MODE_PAYLOAD)
        commands.begin_turn(ctx)
        on_event("compressed", {"stage": "L1", "ok": True})
        events = [event for event, _ in ctx.session_events]
        assert events == ["mode_changed", "compressed"]


def _clear_context(tmp_path) -> SimpleNamespace:
    """_cmd_clear / _cmd_resume 所需的最小 ctx。"""
    ctx = make_fake_ctx()
    ctx.workspace = tmp_path
    ctx.system_prompt = "sp"
    ctx.config = SimpleNamespace(memory_top_n=5, context_limit=128_000, read_only_extra=[])
    ctx.skills = SimpleNamespace(scan=lambda: None, index_text=lambda: "", warnings=[])
    ctx.memory_store = SimpleNamespace(load_injection=lambda n: "")
    ctx.history = SimpleNamespace(view=lambda: [])
    ctx.state = SimpleNamespace(mode="plan", approval_policy="per-action")
    return ctx


class TestSessionBufferReset:
    """会话缓冲重置点收窄（§4.3）：仅 /clear 与 /resume 清空。"""

    @pytest.mark.asyncio
    async def test_clear_resets_session_buffer(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _clear_context(tmp_path)
        ctx.session_events.append(("mode_changed", MODE_PAYLOAD))
        ctx.text_segment.append("残段")
        monkeypatch.setattr(cli, "rebuild_loop", lambda ctx, thinking=None: None)
        monkeypatch.setattr("glaucous.extensions.rules.load_rules", lambda ws: "")
        monkeypatch.setattr("glaucous.ui.prompts.build_system_prompt", lambda *a, **k: "sp")
        monkeypatch.setattr(commands, "History", SimpleNamespace(create=lambda sp, ws: SimpleNamespace(view=lambda: [])))
        assert await commands.handle_command("/clear", ctx) is True
        assert ctx.session_events == []
        assert ctx.text_segment == []

    @pytest.mark.asyncio
    async def test_resume_resets_session_buffer(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _clear_context(tmp_path)
        ctx.session_events.append(("mode_changed", MODE_PAYLOAD))
        monkeypatch.setattr(cli, "rebuild_loop", lambda ctx, thinking=None: None)
        monkeypatch.setattr(cli, "resume_history", lambda ws, rid, sp, renderer: (SimpleNamespace(view=lambda: []), commands.SessionState()))
        assert await commands.handle_command("/resume", ctx) is True
        assert ctx.session_events == []


class TestExpand:
    @pytest.mark.asyncio
    async def test_empty_buffer_hints(self) -> None:
        ctx = make_fake_ctx()
        await commands._cmd_expand(ctx)
        assert any("暂无可展开的思考过程" in note for note in ctx.renderer.notes)

    @pytest.mark.asyncio
    async def test_replay_full_session(self) -> None:
        """全会话重放（§4.1）：分隔头「本会话」+ 伪事件摘要 + 正文段全文 + 工具结果全量。"""
        ctx = make_fake_ctx()
        ctx.session_events.append(("ask", {"summary": "提问「用哪个库」→ 回答：rich"}))
        ctx.session_events.append(("text_segment", {"text": "第一步：先看目录结构\n第二步：定位入口"}))
        ctx.session_events.append(("tool_end", {
            "call": SimpleNamespace(name="list_dir", arguments="{}"),
            "result": SimpleNamespace(ok=True, content="line1\nline2\nline3"),
        }))
        await commands._cmd_expand(ctx)
        joined = "\n".join(ctx.renderer.notes)
        assert "思考过程（本会话）" in joined  # 语义变更：全会话而非上一轮
        assert "提问「用哪个库」→ 回答：rich" in joined
        assert "中间步正文" in joined and "第二步：定位入口" in joined
        assert "line3" in joined  # 工具结果全量重放（不受摘要行数限制）
        assert len(ctx.session_events) == 3  # 只读：缓冲不受影响


class TestCollapseSwitch:
    def test_off_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLAUCOUS_COLLAPSE=off：即使 TTY 也不开折叠（§4.5 开关语义）。"""
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


class TestBudgetThinkingLine:
    """budget 思考区摘要行防回退（r2-B1/r3-S1）：percent 为 0~1 比例，文案须 ×100；
    圆环字符经 ctx_ring 动态取形（与 render_event 同源），不得硬编码。"""

    def test_percent_rendered_as_percentage(self) -> None:
        payload = {"used": 54000, "limit": 128000, "percent": 0.4219, "level": "low"}
        line = cli._thinking_line("budget", payload)
        assert "42%" in line and "54000/128000" in line
        assert "0.4219%" not in line  # 防比例直拼回退（差 100 倍缺陷）
        assert line.startswith("◑")  # 42% 四分位 → ◑（ctx_ring 取形，非硬编码 ◔）

    def test_ring_glyph_follows_ratio(self) -> None:
        assert cli._thinking_line("budget", {"percent": 0.10}).startswith("○")
        assert cli._thinking_line("budget", {"percent": 0.9375}).startswith("●")

    def test_empty_payload_no_raise(self) -> None:
        line = cli._thinking_line("budget", {})
        assert "0%" in line  # 缺省 0.0 兜底不抛错

class TestThinkingRedrawProtocol:
    """v1.1 修订：自管 ANSI 擦除重绘协议（取代 rich.live.Live）的关键行为。"""

    def _wire(self, monkeypatch):
        import io

        from rich.console import Console

        buf = io.StringIO()
        monkeypatch.setattr(cli, "console", Console(file=buf, width=100, height=50))
        return buf

    def test_add_draws_block_and_counts(self, monkeypatch) -> None:
        buf = self._wire(monkeypatch)
        view = cli.ThinkingView()
        view.start()
        view.add("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        assert "⚙ 思考中 · 1 步" in buf.getvalue()
        assert view._drawn and view._last_block == 2  # header + 1 行
        assert view.count == 1

    def test_pause_erases_and_deactivates(self, monkeypatch) -> None:
        buf = self._wire(monkeypatch)
        view = cli.ThinkingView()
        view.start()
        view.add("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        view.pause()
        assert not view.active
        assert "\x1b[2A\x1b[J" in buf.getvalue()  # 擦除动态区（块高 2），交互卡打在原位
        view.resume()
        assert view.active  # 恢复后事件重新收纳

    def test_close_erases_and_leaves_summary(self, monkeypatch) -> None:
        buf = self._wire(monkeypatch)
        view = cli.ThinkingView()
        view.start()
        view.add("tool_end", {
            "call": SimpleNamespace(name="ls", arguments=""),
            "result": SimpleNamespace(ok=True, content="x", metadata={}),
        })
        view.close({"prompt": 0, "completion": 0})
        out = buf.getvalue()
        assert "💭 思考过程（1 步）— /expand 查看" in out
        assert not view._drawn  # 动态区已擦除

    def test_inactive_turn_no_summary(self, monkeypatch) -> None:
        buf = self._wire(monkeypatch)
        view = cli.ThinkingView()  # 未 start（降级/管道轮）
        view.close({"prompt": 0, "completion": 0})
        assert "💭" not in buf.getvalue()

    def test_add_while_paused_prints_directly(self, monkeypatch) -> None:
        buf = self._wire(monkeypatch)
        view = cli.ThinkingView()
        view.start()
        view.pause()
        view.add("tool_start", {"call": SimpleNamespace(name="ls", arguments="")})
        # 暂停期事件降级直打（不进动态区、不留待重绘）
        assert view.count == 1 and not view._lines
