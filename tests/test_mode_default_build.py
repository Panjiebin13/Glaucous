"""v1.1-M1 模式基座单测（spec §7.2，概设 §11 同名）。

覆盖：SessionState 默认值翻转（Build + auto-approve）；底线守卫默认路径；
/build 三分支（无参/per-action/auto-approve 回切）与非法参数不改状态；
/plan 只读语义（写工具 schema 过滤、豁免清空、policy 维持）与 /build 回切；
submit_plan 二选（approve 含锚行 / feedback 回喂反馈 / EOF 归 feedback /
PLAN 下批准切 Build 且经 loop 统一出口发射 mode_changed）；BASE_PROMPT 三断言。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

import pytest

from glaucous import cli
from glaucous.agent.loop import AgentLoop
from glaucous.agent.state import SessionState
from glaucous.commands import handle_command
from glaucous.context.history import History
from glaucous.llm.client import AssistantMessage, ToolCall
from glaucous.permission.approval import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalPipeline,
    AuditLog,
)
from glaucous.permission.modes import (
    MODE_BUILD,
    MODE_PLAN,
    POLICY_AUTO_APPROVE,
    POLICY_PER_ACTION,
    SessionState,
)
from glaucous.permission.risk import Risk
from glaucous.tools.base import Tool, ToolRegistry
from glaucous.tools.planning import (
    CHOICE_APPROVE,
    CHOICE_FEEDBACK,
    PlanDecision,
    SubmitPlanTool,
)
from glaucous.ui.prompts import BASE_PROMPT
from glaucous.ui.renderer import Renderer


# ---------------------------------------------------------------------------
# 测试基建


class FakeRenderer:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.infos: list[str] = []
        self.errors: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


def make_ctx(tmp_path: Path, state: SessionState | None = None) -> SimpleNamespace:
    """handle_command 可用的最小 ctx：state / renderer / audit 三依赖。"""
    return SimpleNamespace(
        state=state or SessionState(),
        renderer=FakeRenderer(),
        audit=AuditLog(tmp_path / "audit.log"),
    )


class ScriptedLLM:
    """按脚本依次返回响应的假 LLM（驱动 loop.run 验证 mode_changed 出口）。"""

    def __init__(self, script: list[AssistantMessage]) -> None:
        self._script = script

    async def chat(self, messages, tools=None, on_text=None):
        return self._script.pop(0)


class _BuildOnlyTool(Tool):
    """仅 Build 可用的哑工具（声明层过滤断言用）。"""

    name = "write_dummy"
    description = "dummy"
    parameters = {"type": "object", "properties": {}}
    modes = frozenset({MODE_BUILD})


# ---------------------------------------------------------------------------
# 默认值与底线守卫默认路径


class TestDefaults:
    def test_default_state_build_auto_approve(self) -> None:
        # v1.1-M1 核心翻转：启动即 Build + 自动放行（spec §2.1）
        state = SessionState()
        assert state.mode == MODE_BUILD
        assert state.approval_policy == POLICY_AUTO_APPROVE
        assert state.approved_types == set()

    def test_write_tools_visible_in_default_mode(self) -> None:
        # 默认模式下写工具声明可见（tool_schemas 含 Build 工具）
        registry = ToolRegistry()
        registry.register(_BuildOnlyTool())
        assert any(s["function"]["name"] == "write_dummy" for s in registry.tool_schemas(MODE_BUILD))


class TestGuardDefaultPath:
    def test_normal_write_silent_and_audited(self, tmp_path: Path) -> None:
        # 底线守卫默认路径：默认构造下普通区内写静默放行（审计 auto_approve）
        audit_path = tmp_path / "audit.log"
        pipeline = ApprovalPipeline(SessionState(), callback=lambda a: ApprovalDecision(choice="reject"), audit=AuditLog(audit_path))
        verdict = pipeline.gate(ApprovalAction(kind="file_write", target="a.py", risk=Risk.WRITE))
        assert verdict.allowed
        assert "auto_approve" in audit_path.read_text(encoding="utf-8")

    def test_dangerous_still_confirmed(self, tmp_path: Path) -> None:
        # 默认策略下 DANGEROUS 永远单独确认（gate 守卫优先级，spec §六）
        pipeline = ApprovalPipeline(
            SessionState(),
            callback=lambda a: ApprovalDecision(choice="reject", reason="不许"),
            audit=AuditLog(tmp_path / "audit.log"),
        )
        verdict = pipeline.gate(ApprovalAction(kind="bash_command", target="git push --force", risk=Risk.DANGEROUS))
        assert not verdict.allowed
        assert "不许" in verdict.message


# ---------------------------------------------------------------------------
# /build 三分支 与 /plan 切换（spec §3.2/§3.3）


class TestBuildCommand:
    @pytest.mark.asyncio
    async def test_per_action_lands_policy(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        assert await handle_command("/build per-action", ctx) is True
        assert ctx.state.mode == MODE_BUILD
        assert ctx.state.approval_policy == POLICY_PER_ACTION
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert "mode_switch" in audit_text and "per-action" in audit_text

    @pytest.mark.asyncio
    async def test_invalid_arg_keeps_state(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        assert await handle_command("/build nonsense", ctx) is True
        assert ctx.state.mode == MODE_BUILD  # 状态不变
        assert ctx.state.approval_policy == POLICY_AUTO_APPROVE
        assert ctx.renderer.errors  # 报用法提示
        assert not (tmp_path / "audit.log").exists()  # 不写审计

    @pytest.mark.asyncio
    async def test_no_arg_in_build_light_note(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        assert await handle_command("/build", ctx) is True
        assert ctx.renderer.notes  # 轻提示「已处于 Build 模式」
        assert not ctx.renderer.infos
        assert not (tmp_path / "audit.log").exists()  # 无实际变化不重复审计

    @pytest.mark.asyncio
    async def test_no_arg_from_plan_keeps_policy(self, tmp_path: Path) -> None:
        # PLAN 下 /build 无参：仅切模式，策略维持（现状为默认 auto-approve）
        state = SessionState()
        state.enter_build(POLICY_PER_ACTION)
        state.enter_plan()
        ctx = make_ctx(tmp_path, state)
        assert await handle_command("/build", ctx) is True
        assert ctx.state.mode == MODE_BUILD
        assert ctx.state.approval_policy == POLICY_PER_ACTION  # 维持不变
        assert any("已进入 Build" in i for i in ctx.renderer.infos)

    @pytest.mark.asyncio
    async def test_auto_approve_switch_back_channel(self, tmp_path: Path) -> None:
        # r1-S3：/build auto-approve 是策略回切通道
        state = SessionState()
        state.enter_build(POLICY_PER_ACTION)
        ctx = make_ctx(tmp_path, state)
        assert await handle_command("/build auto-approve", ctx) is True
        assert ctx.state.approval_policy == POLICY_AUTO_APPROVE
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert "mode_switch" in audit_text and "auto-approve" in audit_text


class TestPlanSwitch:
    @pytest.mark.asyncio
    async def test_plan_hides_write_tools_and_keeps_policy(self, tmp_path: Path) -> None:
        # /plan：只读语义（写工具 schema 过滤）、豁免清空、policy 维持
        state = SessionState()
        state.add_approved_type("file_write")
        registry = ToolRegistry()
        registry.register(_BuildOnlyTool())
        ctx = make_ctx(tmp_path, state)
        ctx.registry = registry  # 供断言用（handle_command 不感知）
        assert await handle_command("/plan", ctx) is True
        assert ctx.state.mode == MODE_PLAN
        assert not ctx.state.approved_types
        assert ctx.state.approval_policy == POLICY_AUTO_APPROVE
        assert registry.tool_schemas(MODE_PLAN) == []  # 写工具声明层不可见
        assert any(s["function"]["name"] == "write_dummy" for s in registry.tool_schemas(MODE_BUILD))

    @pytest.mark.asyncio
    async def test_plan_then_build_returns(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        await handle_command("/plan", ctx)
        assert ctx.state.mode == MODE_PLAN
        assert await handle_command("/build", ctx) is True
        assert ctx.state.mode == MODE_BUILD
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert audit_text.count("mode_switch") == 2  # 两次切换均审计


# ---------------------------------------------------------------------------
# submit_plan 二选（spec §4.1/§4.2/§4.3）


class TestSubmitPlanTool:
    def _tool(self, decision: PlanDecision, plans_dir: Path) -> SubmitPlanTool:
        return SubmitPlanTool(confirm=lambda plan: decision, plans_dir=plans_dir)

    @pytest.mark.asyncio
    async def test_approve_reply_with_anchor(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".glaucous" / "plans"
        result = await self._tool(
            PlanDecision(choice=CHOICE_APPROVE), plans_dir
        ).execute(plan="# 方案\n目标：修复登录\n- 步骤一")
        assert result.ok
        assert "用户已批准方案" in result.content
        assert ".glaucous/plans/" in result.content  # 锚行（落盘机制原样保留）
        assert list(plans_dir.glob("*.md"))  # 方案全文落盘

    @pytest.mark.asyncio
    async def test_feedback_reply_carries_text(self, tmp_path: Path) -> None:
        result = await self._tool(
            PlanDecision(choice=CHOICE_FEEDBACK, feedback="先补测试"), tmp_path
        ).execute(plan="目标：x")
        assert result.ok
        assert "用户未批准" in result.content
        assert "用户反馈：先补测试" in result.content

    @pytest.mark.asyncio
    async def test_feedback_without_text(self, tmp_path: Path) -> None:
        result = await self._tool(
            PlanDecision(choice=CHOICE_FEEDBACK, feedback=None), tmp_path
        ).execute(plan="目标：x")
        assert "用户未附加反馈" in result.content

    @pytest.mark.asyncio
    async def test_approve_in_build_touches_no_state(self, tmp_path: Path) -> None:
        # BUILD 下批准：不触碰 mode/policy（幂等，spec §六）
        state = SessionState()  # 默认 Build
        tool = SubmitPlanTool(
            confirm=lambda plan: (PlanDecision(choice=CHOICE_APPROVE)),  # 不动 state（cli 收敛规则）
            plans_dir=None,
        )
        result = await tool.execute(plan="目标：x")
        assert result.ok
        assert state.mode == MODE_BUILD and state.approval_policy == POLICY_AUTO_APPROVE

    @pytest.mark.asyncio
    async def test_plan_approve_switches_and_emits_mode_changed(self, tmp_path: Path) -> None:
        # r1-B1 断言：PLAN 下批准回 Build，经 loop 统一出口发射 mode_changed
        # （mode=build、policy=维持值）；BUILD 下批准比对为假不发射。
        state = SessionState()
        state.enter_plan()

        def confirm(plan: str) -> PlanDecision:
            # 模拟 cli confirm 收敛规则：PLAN 下批准才 enter_build()
            if state.mode == MODE_PLAN:
                state.enter_build()
            return PlanDecision(choice=CHOICE_APPROVE)

        registry = ToolRegistry()
        registry.register(SubmitPlanTool(confirm=confirm, plans_dir=None))

        tool_call = ToolCall(id="call-1", name="submit_plan", arguments=json.dumps({"plan": "目标：x"}))
        llm = ScriptedLLM([
            AssistantMessage(text="", tool_calls=[tool_call]),   # 第 1 步：提交方案
            AssistantMessage(text="开始执行"),                    # 第 2 步：批准后继续
        ])
        history = History.create("", tmp_path)
        events: list[tuple[str, dict]] = []
        loop = AgentLoop(llm, registry, history, state, on_event=lambda e, p: events.append((e, p)))

        await loop.run("执行方案")

        mode_events = [p for e, p in events if e == "mode_changed"]
        assert len(mode_events) == 1
        assert mode_events[0]["mode"] == "build"
        assert mode_events[0]["policy"] == POLICY_AUTO_APPROVE  # 维持值

        # 对照：BUILD 下批准不发射（mode 比对为假）
        events2: list[tuple[str, dict]] = []
        state2 = SessionState()  # 默认 Build
        registry2 = ToolRegistry()
        registry2.register(SubmitPlanTool(confirm=lambda plan: PlanDecision(choice=CHOICE_APPROVE), plans_dir=None))
        loop2 = AgentLoop(
            ScriptedLLM([
                AssistantMessage(text="", tool_calls=[tool_call]),
                AssistantMessage(text="开始执行"),
            ]),
            registry2, History.create("", tmp_path), state2,
            on_event=lambda e, p: events2.append((e, p)),
        )
        await loop2.run("执行方案")
        assert [e for e, _ in events2 if e == "mode_changed"] == []


class TestPromptPlanDecision:
    @pytest.mark.asyncio
    async def test_eof_maps_to_feedback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # EOF/Ctrl+C → 修改意见（不视为批准，spec §4.3）
        def _raise(*args, **kwargs):
            raise EOFError

        monkeypatch.setattr(cli.console, "input", _raise)
        decision = cli.prompt_plan_decision("方案")
        assert decision.choice == CHOICE_FEEDBACK
        assert decision.feedback is None


# ---------------------------------------------------------------------------
# 默认徽标文案固化（spec §2.3/r1-B1：_mode_badge 双态、toolbar 仅模式态）


class TestModeBadgeDefaultCopy:
    def _renderer(self) -> Renderer:
        return Renderer(Console())

    def test_build_badge_carries_policy_note(self) -> None:
        r = self._renderer()
        assert r._mode_badge("build", POLICY_AUTO_APPROVE).plain == "⬥ build·自动放行"
        assert r._mode_badge("build", POLICY_PER_ACTION).plain == "⬥ build·每次审批"

    def test_plan_badge_mode_only(self) -> None:
        assert self._renderer()._mode_badge("plan", None).plain == "◆ plan"

    def test_toolbar_mode_only_without_policy_note(self) -> None:
        # toolbar 仅模式态（spec §2.3）：policy 附注不进 toolbar
        r = self._renderer()
        line = r.toolbar_text("build", POLICY_AUTO_APPROVE)
        assert "⬥ build" in line and "自动放行" not in line
        assert "◆ plan" in r.toolbar_text("plan", None)


# ---------------------------------------------------------------------------
# BASE_PROMPT 三断言（spec §7.2）


class TestBasePrompt:
    def test_contains_clarify_section(self) -> None:
        assert "先澄清" in BASE_PROMPT

    def test_contains_high_risk_section(self) -> None:
        assert "高风险" in BASE_PROMPT

    def test_no_auto_return_to_plan(self) -> None:
        # 常驻 Build：v1.0 的「任务完成后会自动回到 Plan 模式」句退役
        assert "自动回到 Plan" not in BASE_PROMPT
