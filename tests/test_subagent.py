"""v1.1-M2 多 Agent 基础单测（spec §8.1，计划表任务 2.5）。

覆盖：隔离性（父史仅增 2 条/上下文零污染）、防嵌套（子 registry 无 spawn_agent）、
快照继承（per-action 弹卡 + origin 标注 + 豁免不回流 + submit_plan 批准只翻子副本）、
报告回传（四段/≤400 字/metadata）、子会话文件、事件通道（sub_* 序列 + make_on_event
落账）、串行性、空 task 兜底。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from glaucous import cli
from glaucous.agent.loop import AgentLoop
from glaucous.agent.subagent import SubagentRunner, build_report, build_sub_registry
from glaucous.context.history import History
from glaucous.llm.client import AssistantMessage, ToolCall
from glaucous.permission.approval import (
    ApprovalAction,
    ApprovalDecision,
    AuditLog,
)
from glaucous.permission.modes import (
    MODE_BUILD,
    MODE_PLAN,
    POLICY_PER_ACTION,
    SessionState,
)
from glaucous.permission.risk import Risk
from glaucous.tools.base import Tool, ToolRegistry, ToolResult
from glaucous.tools.spawn_agent import SpawnAgentTool

REPORT_SECTIONS = ("【任务结果摘要】", "【修改文件清单】", "【验证结果】", "【风险与遗留】")


# ---------------------------------------------------------------------------
# 测试基建


class ScriptedLLM:
    """按脚本依次返回响应的假 LLM（order 非空时记录调用次序佐证串行性）。"""

    def __init__(self, script: list[AssistantMessage], order: list[str] | None = None) -> None:
        self._script = list(script)
        self._order = order

    async def chat(self, messages, tools=None, on_text=None):
        if self._order is not None:
            self._order.append("llm")
        return self._script.pop(0)


class StubWriteTool(Tool):
    """哑写工具：构造 file_write 审批动作（per-action 弹卡/快照继承验证）。"""

    name = "write_file"
    description = "stub write"
    parameters = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    }

    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []

    def build_approval(self, args: dict, mode: str) -> ApprovalAction:
        return ApprovalAction(kind="file_write", target=args.get("path", ""), risk=Risk.WRITE)

    async def execute(self, path: str, content: str = "") -> ToolResult:
        self.log.append(path)
        return ToolResult(ok=True, content=f"已写入 {path}")


class StubBashTool(Tool):
    """哑 bash 工具：固定 DANGEROUS 风险（守卫独立确认验证）。"""

    name = "bash"
    description = "stub bash"
    parameters = {
        "type": "object",
        "required": ["command"],
        "properties": {"command": {"type": "string"}},
    }

    def build_approval(self, args: dict, mode: str) -> ApprovalAction:
        return ApprovalAction(
            kind="bash_command", target=args.get("command", ""), risk=Risk.DANGEROUS
        )

    async def execute(self, command: str) -> ToolResult:
        return ToolResult(ok=True, content="")


class ProbeTool(Tool):
    """探针工具：记录派发期间的 active_state 并模拟 confirm 批准路径。"""

    name = "probe"
    description = "probe"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, ctx, record: dict) -> None:
        self._ctx = ctx
        self._record = record

    async def execute(self) -> ToolResult:
        active = self._ctx.active_state or self._ctx.state  # cli.confirm 同款读取
        self._record["active_is_parent"] = active is self._ctx.state
        active.enter_build()  # 模拟 submit_plan 批准（cli.confirm 同款逻辑）
        self._record["mode_after_approve"] = active.mode
        return ToolResult(ok=True, content="probed")


def make_ctx(state: SessionState):
    """runner 归属切换的最小 ctx（鸭子类型，等价 ReplContext 三字段）。"""
    return SimpleNamespace(active_state=None, active_agent="主 agent", active_task="", state=state)


def make_runner(
    tmp_path: Path,
    parent_registry: ToolRegistry,
    state: SessionState,
    llm,
    decision_callback=None,
    events: list | None = None,
    ctx=None,
    outputs_dir: Path | None = None,
) -> tuple[SubagentRunner, SimpleNamespace]:
    ctx = ctx or make_ctx(state)
    runner = SubagentRunner(
        llm=llm,
        parent_registry=parent_registry,
        state=state,
        audit=AuditLog(tmp_path / "audit.log"),
        decision_callback=decision_callback,
        workspace=tmp_path,
        rules="",
        max_steps=10,
        context_limit=128_000,
        outputs_dir=outputs_dir,
        plans_dir=None,
        on_event=(lambda e, p: events.append((e, p))) if events is not None else None,
        ctx=ctx,
    )
    return runner, ctx


def tool_call(call_id: str, name: str, **args) -> AssistantMessage:
    return AssistantMessage(tool_calls=[ToolCall(id=call_id, name=name, arguments=json.dumps(args))])


# ---------------------------------------------------------------------------
# 隔离性（FR-61）与防嵌套（FR-64）


class TestIsolation:
    def test_parent_history_grows_exactly_two(self, tmp_path: Path) -> None:
        # 父视角零污染：spawn 全程父史仅增 assistant(tool_calls)+tool 两条
        state = SessionState()
        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())
        sub_llm = ScriptedLLM(
            [
                tool_call("s1", "write_file", path="a.py", content="x"),
                AssistantMessage(text="done"),
            ]
        )
        runner, _ctx = make_runner(tmp_path, parent_registry, state, sub_llm)
        parent_registry.register(SpawnAgentTool(runner))
        parent_llm = ScriptedLLM(
            [
                tool_call("p1", "spawn_agent", task="写 a.py"),
                AssistantMessage(text="final"),
            ]
        )
        history = History.create("sys", tmp_path)
        loop = AgentLoop(parent_llm, parent_registry, history, state)
        asyncio.run(loop.run("任务"))

        assert len(history.messages) == 4  # user / assistant(tool_calls) / tool / assistant
        tool_entries = [m for m in history.messages if m.get("role") == "tool"]
        assert len(tool_entries) == 1
        # 报告作为工具结果入父史，子过程内容不在父史
        assert all(section in tool_entries[0]["content"] for section in REPORT_SECTIONS)

    def test_no_nested_spawn(self, tmp_path: Path) -> None:
        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())
        parent_registry.register(SpawnAgentTool(object()))  # 占位 runner
        sub_registry = build_sub_registry(parent_registry)
        names = [t.name for t in sub_registry.all_tools()]
        assert "spawn_agent" not in names
        # 执行层兜底：幻觉调用回喂「不存在」
        result = asyncio.run(
            sub_registry.dispatch(ToolCall(id="x", name="spawn_agent", arguments="{}"), MODE_BUILD)
        )
        assert not result.ok
        assert "不存在" in result.content


# ---------------------------------------------------------------------------
# 快照继承与独立审批（FR-62，r1-B1 对应用例）


class TestInheritance:
    def test_per_action_card_and_no_backflow(self, tmp_path: Path) -> None:
        state = SessionState()
        state.enter_build(POLICY_PER_ACTION)
        captured: list[ApprovalAction] = []

        def cb(action: ApprovalAction) -> ApprovalDecision:
            captured.append(action)
            return ApprovalDecision(choice="approve_type")

        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())
        sub_llm = ScriptedLLM(
            [
                tool_call("s1", "write_file", path="a.py"),
                AssistantMessage(text="done"),
            ]
        )
        runner, ctx = make_runner(tmp_path, parent_registry, state, sub_llm, decision_callback=cb)
        result = asyncio.run(runner.run("写 a.py"))

        agent_id = result.metadata["sub_agent"]  # 类级计数：跨用例不自认 child-1（r1-S2）
        assert result.ok
        assert captured[0].origin == agent_id           # 回调侧归属标注（概设 §8.3）
        assert captured[0].origin_task == "写 a.py"
        # 「同意同类型」写入子副本，不回流父（r1-B1 ①反例消除）
        assert state.approved_types == set()
        # 归属哨兵恢复（r2-B4）
        assert ctx.active_state is None
        assert ctx.active_agent == "主 agent"
        assert ctx.active_task == ""
        # 审计归属字段（agent 恒有 / agent_task 非空附）
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert f'"agent": "{agent_id}"' in audit_text
        assert '"agent_task"' in audit_text

    def test_dangerous_confirmed_in_sub(self, tmp_path: Path) -> None:
        # auto-approve 父策略下，子 agent 内 DANGEROUS 仍单独确认（守卫不变）
        state = SessionState()  # build + auto-approve
        decisions: list[ApprovalDecision] = []

        def cb(action: ApprovalAction) -> ApprovalDecision:
            decision = ApprovalDecision(choice="reject", reason="不许")
            decisions.append(decision)
            return decision

        parent_registry = ToolRegistry()
        parent_registry.register(StubBashTool())
        sub_llm = ScriptedLLM(
            [
                tool_call("s1", "bash", command="git push --force"),
                AssistantMessage(text="done"),
            ]
        )
        runner, _ctx = make_runner(tmp_path, parent_registry, state, sub_llm, decision_callback=cb)
        result = asyncio.run(runner.run("危险命令"))
        # 拒绝是控制信号：子内 bash 被拦截回喂，但报告照常回传（ok=True）
        assert result.ok
        assert len(decisions) == 1  # 独立弹卡（经同一回调，含归属标注）
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        sub_agent = result.metadata["sub_agent"]
        assert '"agent": "%s"' % sub_agent in audit_text

    def test_submit_plan_approval_flips_child_copy_only(self, tmp_path: Path) -> None:
        # Plan 模式派发：子内 submit_plan 批准只翻子副本，父模式不受影响（r1-B1 ②）
        state = SessionState(mode=MODE_PLAN)
        record: dict = {}
        ctx = make_ctx(state)
        parent_registry = ToolRegistry()
        parent_registry.register(ProbeTool(ctx, record))
        sub_llm = ScriptedLLM([tool_call("s1", "probe"), AssistantMessage(text="done")])
        runner, _ctx = make_runner(tmp_path, parent_registry, state, sub_llm, ctx=ctx)
        result = asyncio.run(runner.run("评审任务"))

        assert result.ok
        assert record["active_is_parent"] is False        # active_state 是子副本
        assert record["mode_after_approve"] == MODE_BUILD  # 批准翻子副本
        assert state.mode == MODE_PLAN                     # 父会话模式不变
        assert ctx.active_state is None                    # finally 恢复哨兵


# ---------------------------------------------------------------------------
# 报告回传（FR-63）与子会话文件


class TestReport:
    def test_assembled_when_answer_unstructured(self) -> None:
        report = build_report("做完了", ["a.py", "b.py"])
        assert all(section in report for section in REPORT_SECTIONS)
        assert "a.py、b.py" in report
        assert "未执行验证" in report

    def test_preserved_when_answer_structured(self) -> None:
        answer = "【任务结果摘要】ok\n【修改文件清单】无\n【验证结果】pytest 全绿\n【风险与遗留】无"
        assert build_report(answer, []) == answer

    def test_hard_limit_1000_without_outputs_dir(self) -> None:
        # 未配 outputs_dir（内嵌/测试场景）：仅截断，尾注说明存档失败兜底
        report = build_report("长" * 3000, [])
        assert len(report) <= 1000 + len("（报告已截断；完整报告存档失败，可请用户查看子会话文件）")
        assert report.endswith("可请用户查看子会话文件）")

    def test_truncation_archives_full_report(self, tmp_path: Path) -> None:
        # v1.1 用户决策（外置型，对齐 L0/read_output）：超限截断 + 完整报告落盘
        outputs_dir = tmp_path / "outputs"
        answer = "【任务结果摘要】" + "长" * 3000 + "\n【修改文件清单】无\n【验证结果】无\n【风险与遗留】无"
        report = build_report(answer, [], outputs_dir=outputs_dir, call_id="spawn_agent-child-1")
        note = (
            "（报告已截断；完整报告已存档 .glaucous/outputs/spawn_agent-child-1.log，"
            '可调用 read_output(call_id="spawn_agent-child-1") 分段查看）'
        )
        assert len(report) == 1000 + len(note)  # 正文恰 1000 字 + 完整尾注
        assert report.endswith(note)
        log = outputs_dir / "spawn_agent-child-1.log"
        assert log.is_file()
        assert log.read_text(encoding="utf-8") == answer  # 完整报告无损落盘
        # 经 ReadOutputTool 按同一 call_id 可回取（父模型的取回路径）
        from glaucous.tools.output import ReadOutputTool

        result = asyncio.run(ReadOutputTool(outputs_dir).execute(call_id="spawn_agent-child-1"))
        assert result.ok
        assert "长" * 50 in result.content

    def test_metadata_and_session_file(self, tmp_path: Path) -> None:
        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())
        sub_llm = ScriptedLLM(
            [
                tool_call("s1", "write_file", path="a.py"),
                AssistantMessage(text="done"),
            ]
        )
        runner, _ctx = make_runner(tmp_path, parent_registry, SessionState(), sub_llm)
        result = asyncio.run(runner.run("写 a.py"))
        agent_id = result.metadata["sub_agent"]
        assert agent_id.startswith("child-")
        assert result.metadata["modified_files"] == ["a.py"]
        agents_dir = tmp_path / ".glaucous" / "agents"
        files = list(agents_dir.glob("*.jsonl"))
        assert len(files) == 1
        first = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        assert first.get("type") == "session_meta"

    def test_empty_task_rejected_without_session_file(self, tmp_path: Path) -> None:
        parent_registry = ToolRegistry()
        runner, _ctx = make_runner(
            tmp_path, parent_registry, SessionState(), ScriptedLLM([])
        )
        result = asyncio.run(runner.run("   "))
        assert not result.ok
        assert "不能为空" in result.content
        assert not (tmp_path / ".glaucous" / "agents").exists()


# ---------------------------------------------------------------------------
# 事件通道（FR-63/sub_*）与串行性（FR-64）


class TestEventsAndSerial:
    def test_event_sequence(self, tmp_path: Path) -> None:
        events: list[tuple[str, dict]] = []
        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())
        sub_llm = ScriptedLLM(
            [
                tool_call("s1", "write_file", path="a.py"),
                AssistantMessage(text="done"),
            ]
        )
        runner, _ctx = make_runner(
            tmp_path, parent_registry, SessionState(), sub_llm, events=events
        )
        asyncio.run(runner.run("写 a.py"))

        kinds = [e for e, _ in events]
        assert kinds[0] == "sub_start"
        assert kinds[-1] == "sub_end"
        assert "sub_event" in kinds
        agent_id = events[0][1]["agent_id"]
        assert agent_id.startswith("child-")
        assert events[-1][1]["agent_id"] == agent_id
        assert events[-1][1]["ok"] is True
        # 事件不影响父史（runner 不触碰任何 History——由隔离用例共同保证）

    def test_serial_execution_order(self, tmp_path: Path) -> None:
        # 串行：子任务完成后父 loop 才推进（第二次父 chat 在子执行之后）
        order: list[str] = []
        parent_registry = ToolRegistry()
        parent_registry.register(StubWriteTool())

        class OrderProbe(Tool):
            name = "probe"
            description = "probe"
            parameters = {"type": "object", "properties": {}}

            async def execute(self) -> ToolResult:
                order.append("sub-exec")
                return ToolResult(ok=True, content="ok")

        parent_registry.register(OrderProbe())
        sub_llm = ScriptedLLM([tool_call("s1", "probe"), AssistantMessage(text="done")])
        runner, _ctx = make_runner(tmp_path, parent_registry, SessionState(), sub_llm)
        parent_registry.register(SpawnAgentTool(runner))
        parent_llm = ScriptedLLM(
            [
                tool_call("p1", "spawn_agent", task="x"),
                AssistantMessage(text="final"),
            ],
            order=order,
        )
        history = History.create("sys", tmp_path)
        asyncio.run(AgentLoop(parent_llm, parent_registry, history, SessionState()).run("任务"))
        # 父首次 chat → 子执行（probe）→ 父二次 chat
        assert order == ["llm", "sub-exec", "llm"]


# ---------------------------------------------------------------------------
# make_on_event 落账（r1-S4 对应用例）


class TestMakeOnEvent:
    def test_sub_events_recorded_in_session_events(self) -> None:
        ctx = SimpleNamespace(
            text_segment=[],
            session_events=[],
            stream_state={"printed": False},
            last_budget=None,
            state=SessionState(),
        )
        on_event = cli.make_on_event(ctx, None, None)  # type: ignore[arg-type]
        on_event("sub_start", {"agent_id": "child-1", "task": "t"})
        on_event("sub_event", {"agent_id": "child-1", "event": "text", "payload": {"text": "hi"}})
        on_event("sub_end", {"agent_id": "child-1", "brief": "b", "ok": True})
        kinds = [e for e, _ in ctx.session_events]
        assert kinds == ["sub_start", "sub_event", "sub_end"]

    def test_child_text_dedupe_counts_once(self) -> None:
        # r3-S2 回归：同 agent 连续正文增量只计一次思考区（去重生效，N 不灌水）
        class FakeThinking:
            active = True
            was_active = True

            def __init__(self) -> None:
                self.adds: list[tuple[str, dict]] = []

            def add(self, event: str, payload: dict) -> None:
                self.adds.append((event, payload))

        fake = FakeThinking()
        ctx = SimpleNamespace(
            text_segment=[],
            session_events=[],
            stream_state={"printed": False},
            last_budget=None,
            state=SessionState(),
        )
        on_event = cli.make_on_event(ctx, None, fake)  # type: ignore[arg-type]
        for _ in range(50):
            on_event("sub_event", {"agent_id": "child-1", "event": "text", "payload": {"text": "x"}})
        # 思考区只收一条摘要；落账同步去重（v1.1 用户决策）：1 条（/expand 不刷屏）
        assert sum(1 for e, _ in fake.adds if e == "sub_event") == 1
        assert sum(1 for e, _ in ctx.session_events if e == "sub_event") == 1
        # 交替 agent：新 agent 首条再计一次
        on_event("sub_event", {"agent_id": "child-2", "event": "text", "payload": {"text": "y"}})
        assert sum(1 for e, _ in fake.adds if e == "sub_event") == 2
