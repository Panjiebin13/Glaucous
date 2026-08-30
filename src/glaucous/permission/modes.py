"""会话状态：Build/Plan 双模式 + 授权策略 + 同意同类型记录（v1.1-M1，概设 §4.1）。

M1 从 agent/state.py 迁入（Day3 Plan §4.4）；agent/state.py 保留薄壳 re-export 兼容。

状态流转（v1.1：启动即 Build，常驻默认）：
- 启动 → build（policy=auto-approve，默认授权策略）
- build --/plan--> plan（显式进入只读研究模式；豁免清空，策略不变）
- plan --submit_plan 批准 或 /build--> build（策略不变，仅切模式）
- 「任务完成自动回归 Plan」自 v1.1 退役：Build 自然终止后常驻 Build（r1-B1 裁决：
  dispatch 统一出口的 mode_changed 发射保留，服务 submit_plan 批准回 Build 场景）

approved_types：记录「同意同类型」已豁免的操作类型（工具粒度：write_file/edit_file/bash/file_read）；
enter_plan 清空（策略作用域=构建期）。DANGEROUS 与区外写不受同类型豁免
（守卫优先级，Day3 Plan §4.3 S1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 会话模式常量定义在此（单一出口）：tools/base.py 引用本模块而非反向，
# 避免 tools → permission → modes → tools 循环导入（Day3 结构决策）。
MODE_PLAN = "plan"
MODE_BUILD = "build"
ALL_MODES = frozenset({MODE_PLAN, MODE_BUILD})

# 授权策略（概设 v1.1 §4.1：auto-approve 为默认策略；per-action 经 /build per-action 显式选择）
POLICY_PER_ACTION = "per-action"
POLICY_AUTO_APPROVE = "auto-approve"


@dataclass
class SessionState:
    """会话级状态机：mode + approval_policy + approved_types。"""

    mode: str = MODE_BUILD
    approval_policy: str = POLICY_AUTO_APPROVE
    approved_types: set[str] = field(default_factory=set)

    def enter_build(self, policy: str | None = None) -> None:
        """进入 Build 并可选地显式设授权策略；清空豁免记录。

        policy=None（模式切换默认）：维持现有 approval_policy 不变——
        v1.1 中模式切换不重置策略，策略仅经 /build per-action / /build
        auto-approve 显式改变。传入具体策略时落位为该值。
        """
        self.mode = MODE_BUILD
        if policy is not None:
            self.approval_policy = policy
        self.approved_types.clear()

    def enter_plan(self) -> None:
        """显式进入 Plan 研究模式：清空豁免记录，策略不变（策略仅对构建期有意义）。"""
        self.mode = MODE_PLAN
        self.approved_types.clear()

    def add_approved_type(self, operation_type: str) -> None:
        """记录「同意同类型」豁免的操作类型。"""
        self.approved_types.add(operation_type)

    def is_type_approved(self, operation_type: str) -> bool:
        """该操作类型是否已被「同意同类型」豁免。"""
        return operation_type in self.approved_types
