"""会话状态：Plan/Build 双模式 + 授权策略（概设 §5.1/§5.2 的 Day 2 子集）。

结构归属说明：概设 §10 将 plan/build 状态归 permission/modes.py——
M1 权限成型时本模块迁往 permission/modes.py 并吸收切换协议；
Day 2 先以最小状态机形态放 agent 层（loop 直接消费）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tools.base import MODE_BUILD, MODE_PLAN

# 授权策略（概设 §5.2 三选一的前两项语义；策略仅对当前构建过程有效）
POLICY_PER_ACTION = "per-action"
POLICY_AUTO_APPROVE = "auto-approve"


@dataclass
class SessionState:
    """会话级状态机：mode 与 approval_policy。

    状态流转：
    - plan --submit_plan 三选一①/②--> build（policy 分别为 per-action/auto-approve）
    - build --自然终止（任务完成）--> plan（policy 重置，概设 §5.1「任务完成后自动回归」）
    - 异常终止（步数上限/熔断/Ctrl+C）不回归：留在 build 便于用户驱动未竟构建

    resume 恢复会话时重置为 plan/per-action（概设 §5.2「策略不跨会话持久化」，
    重置到 plan 是安全侧且闭环成立：历史含方案全文，模型可再次 submit_plan）。
    """

    mode: str = MODE_PLAN
    approval_policy: str = POLICY_PER_ACTION

    def enter_build(self, policy: str) -> None:
        """三选一①②：进入 Build 并记录授权策略。"""
        self.mode = MODE_BUILD
        self.approval_policy = policy

    def return_to_plan(self) -> None:
        """任务完成回归 Plan：授权策略一并重置（策略作用域=本次构建，概设 §5.2）。"""
        self.mode = MODE_PLAN
        self.approval_policy = POLICY_PER_ACTION
