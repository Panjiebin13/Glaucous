"""权限体系子包：工作区沙箱、危险命令分类器、审批管线、会话状态（概设 §10）。

安全体系四正交概念（概设 §5）：会话模式（plan/build）、授权策略（per-action/auto-approve）、
操作危险级别（SAFE/WRITE/DANGEROUS）、工作区边界（区内/区外）。权限执行分声明层（tool_schemas
过滤）与执行层（沙箱→分类→审批）双层。
"""

# 统一风险枚举单一出口：所有模块（workspace/classifier/approval/base）从这里导入，
# 杜绝多套字面量导致守卫失效（Day3 Plan §3 B1 修复）
from .risk import Risk  # noqa: F401
