"""审批三选项单测（任务 1.7 / 债务项「审批三选项」+ 状态迁移生命周期）。

覆盖：approve/approve_type/reject 各自状态与回喂；同类型放行后不再询问；
拒绝附理由回喂；DANGEROUS 不受同类型豁免仍逐条确认；bash 细分豁免粒度；
approved_types 生命周期（enter_build/return_to_plan 清空）；审计落盘与写失败不阻断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from glaucous.permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from glaucous.permission.modes import POLICY_PER_ACTION, MODE_BUILD, SessionState
from glaucous.permission.risk import Risk


@pytest.fixture()
def state() -> SessionState:
    state = SessionState()
    state.enter_build(POLICY_PER_ACTION)
    return state


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / ".glaucous" / "audit.log"


def make_pipeline(state: SessionState, audit_path: Path, decisions: list[ApprovalDecision]) -> tuple[ApprovalPipeline, list[ApprovalAction]]:
    """回调按序返回预置决策，并记录被询问过的 action（供断言询问次数）。"""
    asked: list[ApprovalAction] = []

    def decide(action: ApprovalAction) -> ApprovalDecision:
        asked.append(action)
        return decisions[min(len(asked) - 1, len(decisions) - 1)]

    pipeline = ApprovalPipeline(state, callback=decide, audit=AuditLog(audit_path))
    return pipeline, asked


def write_action(target: str = "src/a.py", risk: Risk = Risk.WRITE) -> ApprovalAction:
    return ApprovalAction(kind="file_write", target=target, risk=risk)


class TestThreeOptions:
    def test_approve(self, state: SessionState, audit_path: Path) -> None:
        pipeline, asked = make_pipeline(state, audit_path, [ApprovalDecision(choice="approve")])
        verdict = pipeline.gate(write_action())
        assert verdict.allowed
        assert len(asked) == 1

    def test_reject_with_reason_feedback(self, state: SessionState, audit_path: Path) -> None:
        pipeline, _ = make_pipeline(state, audit_path, [ApprovalDecision(choice="reject", reason="路径不对")])
        verdict = pipeline.gate(write_action())
        assert not verdict.allowed
        assert "路径不对" in verdict.message

    def test_approve_type_grants_exemption(self, state: SessionState, audit_path: Path) -> None:
        pipeline, asked = make_pipeline(state, audit_path, [ApprovalDecision(choice="approve_type")])
        assert pipeline.gate(write_action("a.py")).allowed
        # 同类型第二次：不再询问回调（豁免生效）
        assert pipeline.gate(write_action("b.py")).allowed
        assert len(asked) == 1
        assert state.is_type_approved("file_write")

    def test_bash_granular_by_risk(self, state: SessionState, audit_path: Path) -> None:
        # bash 豁免按风险细分：bash:write 豁免不放行 bash:dangerous
        pipeline, asked = make_pipeline(state, audit_path, [ApprovalDecision(choice="approve_type")])
        normal = ApprovalAction(kind="bash_command", target="git add .", risk=Risk.WRITE)
        assert pipeline.gate(normal).allowed
        assert state.is_type_approved("bash:write")
        dangerous = ApprovalAction(kind="bash_command", target="rm -rf /", risk=Risk.DANGEROUS)
        assert pipeline.gate(dangerous).allowed  # 本次单独放行
        assert len(asked) == 2  # DANGEROUS 仍被逐条询问
        assert not state.is_type_approved("bash:dangerous")

    def test_dangerous_approve_type_not_batch_exempt(self, state: SessionState, audit_path: Path) -> None:
        # S1：DANGEROUS 即使选「同意同类型」也不记录豁免，下次仍逐条确认
        pipeline, asked = make_pipeline(state, audit_path, [ApprovalDecision(choice="approve_type")])
        action = write_action("dangerous.py", risk=Risk.DANGEROUS)
        assert pipeline.gate(action).allowed
        assert pipeline.gate(action).allowed
        assert len(asked) == 2
        assert not state.approved_types


class TestNoCallback:
    def test_no_callback_rejects(self, state: SessionState, audit_path: Path) -> None:
        pipeline = ApprovalPipeline(state, callback=None, audit=AuditLog(audit_path))
        verdict = pipeline.gate(write_action())
        assert not verdict.allowed
        assert "拒绝" in verdict.message


class TestStateLifecycle:
    def test_enter_build_clears_previous_types(self, audit_path: Path) -> None:
        state = SessionState()
        state.enter_build(POLICY_PER_ACTION)
        state.add_approved_type("file_write")
        state.enter_build(POLICY_PER_ACTION)  # 新一轮构建
        assert not state.approved_types

    def test_return_to_plan_resets(self, state: SessionState, audit_path: Path) -> None:
        state.add_approved_type("file_write")
        state.return_to_plan()
        assert state.mode != MODE_BUILD
        assert state.approval_policy == POLICY_PER_ACTION
        assert not state.approved_types


class TestAudit:
    def test_decisions_recorded(self, state: SessionState, audit_path: Path, tmp_path: Path) -> None:
        pipeline, _ = make_pipeline(
            state,
            audit_path,
            [ApprovalDecision(choice="approve"), ApprovalDecision(choice="reject", reason="不行")],
        )
        pipeline.gate(write_action())
        pipeline.gate(write_action())
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
        assert [e["decision"] for e in events] == ["approve", "reject"]
        assert events[0]["allowed"] is True and events[1]["allowed"] is False
        assert events[1]["reason"] == "不行"
        assert events[0]["kind"] == "file_write"

    def test_record_denial_for_plan_block(self, state: SessionState, audit_path: Path) -> None:
        pipeline = ApprovalPipeline(state, callback=None, audit=AuditLog(audit_path))
        pipeline.record_denial(write_action(), "Plan 模式只读")
        event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
        assert event["decision"] == "plan_mode_blocked"
        assert event["allowed"] is False

    def test_write_failure_not_blocking(self, tmp_path: Path) -> None:
        busy = tmp_path / "busy"
        busy.mkdir()  # 用目录路径冒充不可写的审计文件
        audit = AuditLog(busy)
        audit.record({"decision": "approve"})  # 不应抛异常
