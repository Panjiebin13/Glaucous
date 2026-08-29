"""会话状态薄壳：内容已迁往 permission/modes.py（M1 任务，概设 §10）。

保留此模块为 re-export，避免 loop/cli/planning 引用大范围改动；
后续 M3 若做状态清理可再精简。
"""

from __future__ import annotations

from ..permission.modes import (  # noqa: F401
    POLICY_AUTO_APPROVE,
    POLICY_PER_ACTION,
    SessionState,
)
