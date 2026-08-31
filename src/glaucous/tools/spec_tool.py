"""read_spec 工具（v1.1-M5，FR-56；spec §3.4）。

Spec 轻量锚的回取通道（与 read_plan 同款机制，概设 §7.4）：执行期
Spec 以锚行内联于任务消息，全文经本工具按需回读。只在主 registry
注册一次——子 registry 由 build_sub_registry 自父派生自然继承（r1-S9）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..permission.modes import ALL_MODES
from ..permission.risk import Risk
from ..safety.output_limit import sanitize_call_id
from ..spec.store import SpecStateError, SpecStore
from .base import Tool, ToolResult


class ReadSpecTool(Tool):
    """回读 Spec 文档全文（frontmatter 摘要 + 正文）。"""

    name = "read_spec"
    description = (
        "回读 Spec 文档全文：缺省读取最新活跃 Spec，也可指定 spec_id。"
        "Spec 全文不常驻上下文，需要确认需求边界/任务清单/验收标准时调用本工具回读。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "spec_id": {
                "type": "string",
                "description": "Spec ID（.glaucous/specs/ 下文件名主体；省略时读最新活跃 Spec）",
            },
        },
        "required": [],
    }
    risk = Risk.SAFE
    modes = ALL_MODES

    def __init__(self, workspace: Path):
        self._store = SpecStore(workspace)

    async def execute(self, spec_id: str = "", **_: Any) -> ToolResult:
        try:
            if spec_id.strip():
                doc = self._store.load(sanitize_call_id(spec_id.strip()))
            else:
                doc = self._store.active()
                if doc is None:
                    docs = self._store.list_all()
                    if not docs:
                        return ToolResult(ok=False, content="尚无 Spec 文档（可用 /spec 发起）。")
                    doc = docs[0]  # 无活跃 → 最新任意状态（§3.4）
            meta = doc.meta
            summary = (
                f"Spec：{doc.spec_id}（{doc.name}）· 状态：{doc.status} · "
                f"轮次：{meta.get('round', 0)}"
            )
            return ToolResult(
                ok=True,
                content=f"{summary}\n文档：.glaucous/specs/{doc.path.name}\n\n{doc.body}",
            )
        except SpecStateError as exc:
            return ToolResult(ok=False, content=str(exc))
        except OSError as exc:
            return ToolResult(ok=False, content=f"读取 Spec 失败：{exc}")
