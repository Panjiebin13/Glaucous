"""事实记忆写入工具：memory_save（任务 2.2，FR-19/21，概设 §7.2）。

设计要点（Day4 Plan §4.2）：
- 两模式可用（记忆沉淀不依赖写权限；写入的是系统内部存储而非工作区文件，
  risk=SAFE 不触发审批——类比 audit.log 的系统写入面）；
- 参数校验在 execute 内完成（scope 枚举由 schema 校验兜底 + 工具内二次校验）；
- 去重语义由 MemoryStore.add 承担（同作用域同 content 刷新 last_used）。
"""

from __future__ import annotations

from typing import Any

from ..extensions.memory import MemoryStore
from .base import Tool, ToolResult

SCOPES = ("global", "project")


class MemorySaveTool(Tool):
    """写入一条事实记忆到指定作用域，供后续会话注入复用。"""

    name = "memory_save"
    description = (
        "保存一条环境事实或经验到长期记忆（跨会话生效）。"
        "适用于：解释器/JDK 路径、构建命令、用户偏好等可复用事实。"
        "scope=project 仅当前项目可见，scope=global 跨项目可见。"
        "注意：只存事实本身，不要存临时性内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "记忆内容：一条具体、自包含的事实（如「本项目解释器在 ~/.venvs/p311/bin/python」）",
            },
            "scope": {
                "type": "string",
                "enum": ["global", "project"],
                "description": "作用域：project=仅当前项目，global=跨项目通用",
            },
            "category": {
                "type": "string",
                "description": "分类标签（可选，如 env/build/test/pref）",
            },
        },
        "required": ["content", "scope"],
    }

    def __init__(self, store: MemoryStore):
        self._store = store

    async def execute(
        self, content: str = "", scope: str = "", category: str = "", **_: Any
    ) -> ToolResult:
        if not content.strip():
            return ToolResult(ok=False, content="content 不能为空，请给出一条具体、自包含的事实。")
        if scope not in SCOPES:
            return ToolResult(ok=False, content=f"scope 必须是 {'/'.join(SCOPES)} 之一，收到：{scope!r}")
        is_new = self._store.add(content.strip(), scope, category.strip())
        action = "已新增" if is_new else "已存在（同内容），已刷新使用时间"
        scope_label = "项目" if scope == "project" else "全局"
        return ToolResult(ok=True, content=f"{action}一条{scope_label}记忆：{content.strip()}")
