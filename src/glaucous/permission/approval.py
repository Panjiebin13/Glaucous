"""审批管线：三选项决策 + 同意同类型 + auto-approve 守卫 + 审计日志（任务 1.3/1.4/1.6）。

设计要点（概设 §5.3，Day3 Plan §4.3/§4.6）：
- ApprovalPipeline.gate(action) 是执行层权限入口：per-action 走三选项；auto-approve
  自动放行但 DANGEROUS/区外读仍单独确认（FR-10 设计底线）；
- 「同意同类型」豁免记录在 SessionState.approved_types；DANGEROUS 与区外写不受豁免
  （守卫优先级高于同类型，S1）；
- 决策回调由 CLI 注入（DecisionCallback），工具层不感知终端；
- AuditLog 记录所有权限决策（三选项/auto-approve 放行/Plan 拦截/逃逸硬拦截），
  写入失败不阻断主流程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from .modes import POLICY_AUTO_APPROVE, POLICY_PER_ACTION, SessionState
from .risk import Risk

# 操作类型（同意同类型的豁免粒度，概设 §5.3「按工具粒度」）
OperationType = Literal["file_read", "file_write", "bash_command"]


@dataclass
class ApprovalAction:
    """一次需要权限判定的操作。

    origin/origin_task（v1.1-M2，FR-62）：归属标注，由 ApprovalPipeline.gate
    在回调前 stamp（默认 "main" 不变主 agent 行为），供回调侧/测试消费。
    """

    kind: OperationType          # 操作类型（file_read/file_write/bash_command）
    target: str                  # 文件路径 或 命令全文
    detail: str = ""             # diff / 分类说明
    risk: Risk = Risk.WRITE      # 统一风险枚举（B1 修复：与分类器/沙箱共用 Risk）
    origin: str = "main"         # 归属 agent 标识（概设 §8.3：main / child-N）
    origin_task: str = ""        # 归属父任务摘要（子 agent 派发时非空）

    @property
    def operation_type(self) -> str:
        """豁免类型标识（bash 按风险再细分，防止 DANGEROUS 被普通 bash 豁免）。"""
        if self.kind == "bash_command":
            # bash 细分为「只读/写/破坏性」——DANGEROUS 不受同类型豁免（守卫优先级）
            return f"bash:{self.risk.value}"
        return self.kind


@dataclass
class ApprovalDecision:
    """用户三选项决策。"""

    choice: Literal["approve", "approve_type", "reject", "reject_rollback"]
    reason: str | None = None


@dataclass
class ApprovalVerdict:
    """审批结果：allowed 决定是否放行，message 回喂给模型。"""

    allowed: bool
    decision: ApprovalDecision | None = None
    message: str = ""


# 决策回调：CLI 注入（per-action 弹三选项；auto-approve 场景由 gate 内部判断）
DecisionCallback = Callable[[ApprovalAction], ApprovalDecision]


class ApprovalPipeline:
    """执行层审批管线。

    agent_label/agent_task（v1.1-M2，FR-62）：子 agent 归属标签——
    gate 调回调前 stamp 到 ApprovalAction；_event 写入审计归属字段。
    默认值 = 主 agent（main），既有构造行为不变。
    """

    def __init__(
        self,
        state: SessionState,
        callback: DecisionCallback | None,
        audit: "AuditLog",
        agent_label: str = "main",
        agent_task: str = "",
    ):
        self._state = state
        self._callback = callback
        self._audit = audit
        self._agent_label = agent_label
        self._agent_task = agent_task

    def gate(self, action: ApprovalAction) -> ApprovalVerdict:
        """审批入口：按策略返回放行/拦截，并审计决策。"""
        # 守卫优先级（v1.1 修订，用户决策 2026-08-30）：仅 DANGEROUS 不可被
        # auto-approve/同类型批量豁免。区外读（WRITE+file_read）从守卫清单移除——
        # 读无完整性风险，与「区内读一律放行」的新权限矩阵一致：auto-approve 下
        # 自动放行；per-action 下弹卡且可「同意同类型」；区外写仍为 DANGEROUS 不变
        always_guard = action.risk == Risk.DANGEROUS

        # auto-approve：非守卫操作自动放行
        if self._state.approval_policy == POLICY_AUTO_APPROVE and not always_guard:
            self._audit.record(self._event(action, "auto_approve", allowed=True))
            return ApprovalVerdict(
                allowed=True,
                message=f"auto-approve 放行：{action.kind} {action.target}",
            )

        # 同类型豁免（仅非守卫操作；DANGEROUS 即使已同意同类型仍逐条确认）
        if (
            not always_guard
            and self._state.is_type_approved(action.operation_type)
            and self._state.approval_policy == POLICY_PER_ACTION
        ):
            self._audit.record(self._event(action, "type_approved", allowed=True))
            return ApprovalVerdict(
                allowed=True,
                message=f"已同意同类型，放行：{action.operation_type} {action.target}",
            )

        # 无回调（非交互场景）→ 拒绝并回喂（安全侧，宁缺毋滥）
        if self._callback is None:
            self._audit.record(self._event(action, "reject_no_callback", allowed=False))
            return ApprovalVerdict(
                allowed=False,
                message=f"操作 {action.kind} {action.target} 未获授权（无审批通道），已拒绝。",
            )

        # 归属标注（v1.1-M2，FR-62）：回调前 stamp，卡面/测试可读；
        # auto-approve 等不放行到 callback 的路径无需标注（无副作用）
        action.origin = self._agent_label
        action.origin_task = self._agent_task
        decision = self._callback(action)
        if decision.choice == "approve":
            self._audit.record(self._event(action, "approve", allowed=True))
            return ApprovalVerdict(allowed=True, decision=decision, message="用户已同意该操作。")
        if decision.choice == "approve_type":
            # DANGEROUS 不可被「同意同类型」批量豁免（守卫优先级，S1）——
            # 本次仍可放行，但不记录豁免，避免后续同类型 DANGEROUS 被批量放行
            if action.risk == Risk.DANGEROUS:
                self._audit.record(self._event(action, "approve", allowed=True))
                return ApprovalVerdict(
                    allowed=True,
                    decision=ApprovalDecision(choice="approve", reason="DANGEROUS 不可批量豁免，本次单独放行"),
                    message="用户已同意该操作（DANGEROUS 不可批量豁免，本次单独放行）。",
                )
            self._state.add_approved_type(action.operation_type)
            self._audit.record(self._event(action, "approve_type", allowed=True))
            return ApprovalVerdict(
                allowed=True,
                decision=decision,
                message=f"用户已同意，并放行同类型操作（{action.operation_type}）。",
            )
        # v1.1-M4（FR-43）：拒绝并回退——文件回退由回调侧执行（UI 层职责，
        # spec 决策 4），gate 仅映射拒绝语义：审计 + 回喂
        if decision.choice == "reject_rollback":
            reason_rb = decision.reason or "未提供理由"
            self._audit.record(self._event(action, "reject_rollback", allowed=False, reason=reason_rb))
            return ApprovalVerdict(
                allowed=False,
                decision=decision,
                message=f"用户拒绝并已回退：{reason_rb}",
            )
        # reject
        reason = decision.reason or "未提供理由"
        self._audit.record(self._event(action, "reject", allowed=False, reason=reason))
        return ApprovalVerdict(
            allowed=False,
            decision=decision,
            message=f"用户已拒绝：{reason}",
        )

    def record_denial(self, action: ApprovalAction, reason: str) -> None:
        """记录未经 gate 的权限拦截（如 Plan 模式写拦截）——审计留痕（概设 §4.6 S3，FR-16）。"""
        self._audit.record(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "mode": self._state.mode,
                "policy": self._state.approval_policy,
                "kind": action.kind,
                "target": action.target,
                "risk": action.risk.value,
                "decision": "plan_mode_blocked",
                "reason": reason,
                "allowed": False,
            }
        )

    # -- 审计 --------------------------------------------------------------

    def _event(self, action: ApprovalAction, decision_kind: str, *, allowed: bool, reason: str | None = None) -> dict:
        """生成审计事件（概设 §5.3：时间/模式/策略/操作/风险/用户选择/是否放行；

        v1.1-M2 增归属字段：agent 恒有（main / child-N，概设 §8.3），
        agent_task 非空时附——主 agent 事件保持既有字段集不变。
        """
        event = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "mode": self._state.mode,
            "policy": self._state.approval_policy,
            "kind": action.kind,
            "target": action.target,
            "risk": action.risk.value,
            "decision": decision_kind,
            "reason": reason,
            "allowed": allowed,
            "agent": self._agent_label,
        }
        if self._agent_task:
            event["agent_task"] = self._agent_task
        return event


class AuditLog:
    """审计日志：追加写 .glaucous/audit.log（JSON 行），写入失败尽力而为。"""

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict) -> None:
        """追加写审计事件；IO 或序列化失败不阻断主流程（审计是留痕不是门禁）。"""
        try:
            with self._path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except (OSError, TypeError):
            pass
