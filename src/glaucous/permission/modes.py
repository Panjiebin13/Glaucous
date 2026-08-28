"""会话状态：Plan/Build 双模式 + 授权策略 + 同意同类型记录（概设 §10 modes.py）。

M1 从 agent/state.py 迁入（Day3 Plan §4.4）；agent/state.py 保留薄壳 re-export 兼容。

状态流转：
- plan --submit_plan 三选一①/②--> build（policy 分别为 per-action/auto-approve）
- build --自然终止（任务完成）--> plan（policy 重置，概设 §5.1「任务完成后自动回归」）
- 异常终止（步数上限/熔断/Ctrl+C）不回归：留在 build 便于用户驱动未竟构建

approved_types：记录「同意同类型」已豁免的操作类型（工具粒度：write_file/edit_file/bash/file_read）；
return_to_plan 清空（策略作用域=本次构建，概设 §5.2）。DANGEROUS 与区外写不受同类型豁免
（守卫优先级，Day3 Plan §4.3 S1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 会话模式常量定义在此（单一出口）：tools/base.py 引用本模块而非反向，
# 避免 tools → permission → modes → tools 循环导入（Day3 结构决策）。
MODE_PLAN = "plan"
MODE_BUILD = "build"
ALL_MODES = frozenset({MODE_PLAN, MODE_BUILD})

# 授权策略（概设 §5.2 三选一的前两项语义；策略仅对当前构建过程有效）
POLICY_PER_ACTION = "per-action"
POLICY_AUTO_APPROVE = "auto-approve"


@dataclass
class SessionState:
    """会话级状态机：mode + approval_policy + approved_types。"""

    mode: str = MODE_PLAN
    approval_policy: str = POLICY_PER_ACTION
    approved_types: set[str] = field(default_factory=set)

    def enter_build(self, policy: str) -> None:
        """三选一①②：进入 Build 并记录授权策略；清空上轮豁免记录。"""
        self.mode = MODE_BUILD
        self.approval_policy = policy
        self.approved_types.clear()

    def return_to_plan(self) -> None:
        """任务完成回归 Plan：授权策略与同类型豁免一并重置（策略作用域=本次构建）。"""
        self.mode = MODE_PLAN
        self.approval_policy = POLICY_PER_ACTION
        self.approved_types.clear()

    def add_approved_type(self, operation_type: str) -> None:
        """记录「同意同类型」豁免的操作类型。"""
        self.approved_types.add(operation_type)

    def is_type_approved(self, operation_type: str) -> bool:
        """该操作类型是否已被「同意同类型」豁免。"""
        return operation_type in self.approved_types
