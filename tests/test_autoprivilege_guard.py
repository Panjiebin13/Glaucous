"""auto-approve 守卫单测（任务 1.7 / 债务项「auto-approve 守卫」，FR-10 设计底线）。

覆盖：auto-approve 放行区内写；仍拦 DANGEROUS；仍拦区外读（file_read 载体）；
同类型豁免不越过守卫优先级。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glaucous.permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from glaucous.permission.modes import POLICY_AUTO_APPROVE, SessionState
from glaucous.permission.risk import Risk


@pytest.fixture()
def auto_state() -> SessionState:
    state = SessionState()
    state.enter_build(POLICY_AUTO_APPROVE)
    return state


def make_pipeline(state: SessionState, audit_path: Path, decisions: list[ApprovalDecision]):
    asked: list[ApprovalAction] = []

    def decide(action: ApprovalAction) -> ApprovalDecision:
        asked.append(action)
        return decisions[min(len(asked) - 1, len(decisions) - 1)]

    return ApprovalPipeline(state, callback=decide, audit=AuditLog(audit_path)), asked


class TestAutoApprove:
    def test_inside_write_silent(self, auto_state: SessionState, tmp_path: Path) -> None:
        # 场景 C：其余区内写操作静默执行
        pipeline, asked = make_pipeline(auto_state, tmp_path / "audit.log", [])
        verdict = pipeline.gate(ApprovalAction(kind="file_write", target="src/a.py", risk=Risk.WRITE))
        assert verdict.allowed
        assert "auto-approve" in verdict.message
        assert asked == []  # 不打扰用户

    def test_dangerous_still_confirmed(self, auto_state: SessionState, tmp_path: Path) -> None:
        # 场景 C：git push --force 仍弹醒目确认
        pipeline, asked = make_pipeline(
            auto_state, tmp_path / "audit.log", [ApprovalDecision(choice="reject", reason="不许强推")]
        )
        action = ApprovalAction(kind="bash_command", target="git push --force", risk=Risk.DANGEROUS)
        verdict = pipeline.gate(action)
        assert not verdict.allowed
        assert len(asked) == 1

    def test_outside_read_still_confirmed(self, auto_state: SessionState, tmp_path: Path) -> None:
        # 场景 C：读取工作区外配置仍需单独同意（file_read + WRITE → 守卫）
        pipeline, asked = make_pipeline(
            auto_state, tmp_path / "audit.log", [ApprovalDecision(choice="approve")]
        )
        action = ApprovalAction(kind="file_read", target="D:/outside/cfg.yml", risk=Risk.WRITE)
        verdict = pipeline.gate(action)
        assert verdict.allowed  # 用户单独同意后放行
        assert len(asked) == 1

    def test_dangerous_approve_no_type_leak(self, auto_state: SessionState, tmp_path: Path) -> None:
        # auto-approve 下单独确认的 DANGEROUS 也不产生同类型豁免
        pipeline, _ = make_pipeline(auto_state, tmp_path / "audit.log", [ApprovalDecision(choice="approve")])
        action = ApprovalAction(kind="bash_command", target="rm -rf /", risk=Risk.DANGEROUS)
        assert pipeline.gate(action).allowed
        assert not auto_state.approved_types

    def test_auto_approve_audited(self, auto_state: SessionState, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.log"
        pipeline, _ = make_pipeline(auto_state, audit_path, [])
        pipeline.gate(ApprovalAction(kind="file_write", target="a.py", risk=Risk.WRITE))
        assert "auto_approve" in audit_path.read_text(encoding="utf-8")
