"""统一风险枚举（单一出口）。

概设 §5.5 危险级别分类；所有权限模块从这里导入 Risk，
消除多套字面量导致 auto-approve 守卫失效的风险（Day3 Plan §3 B1 修复）。
"""

from __future__ import annotations

from enum import Enum


class Risk(Enum):
    """操作危险级别（概设 §5.5）。"""

    SAFE = "safe"          # 白名单只读语义，Plan/Build 均放行
    WRITE = "write"        # 修改区内状态，Build 审批（按策略）
    DANGEROUS = "dangerous"  # 模式表命中 / 区外写，永远单独醒目确认
