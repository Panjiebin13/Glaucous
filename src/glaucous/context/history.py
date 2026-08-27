"""对话历史：OpenAI 角色语义的消息模型 + view() API 格式转换。

消息序列硬约束（OpenAI 协议）：role=tool 消息必须紧跟包含对应
tool_call_id 的 assistant(tool_calls) 消息之后——因此主循环在
dispatch 之前先 push_assistant，push_tool 携带 call_id 配对入史。

持久化（会话 JSONL 落盘与 --resume）是 Day 2 任务 0.14，本版本仅内存态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm.client import AssistantMessage, ToolCall
from ..tools.base import ToolResult


@dataclass
class ToolMessage:
    """入史形态的工具结果：call_id/name 与 content/ok 打包，保证 view() 可生成合法序列。"""

    call_id: str
    name: str
    content: str
    ok: bool


@dataclass
class History:
    """自管理的对话历史。

    CLI 与 AgentLoop 共享同一实例，REPL 跨轮次累积（Plan §4.4）；
    每轮 run() 追加 user + assistant/tool 消息，多轮上下文连续。
    """

    system_prompt: str
    _messages: list[dict[str, Any]] = field(default_factory=list)

    def push_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def push_assistant(self, msg: AssistantMessage) -> None:
        """assistant 消息入史：文本与 tool_calls 可同时存在。

        必须在 dispatch 之前调用（tool 消息的配对前提）。
        """
        entry: dict[str, Any] = {"role": "assistant"}
        # content 显式置 None：部分网关拒绝缺失 content 键的 assistant 消息
        entry["content"] = msg.text
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in msg.tool_calls
            ]
        self._messages.append(entry)

    def push_tool(self, call: ToolCall, result: ToolResult) -> None:
        """工具结果入史：失败结果同样入史（错误即控制信号，模型据此自纠）。"""
        self._messages.append(self._tool_entry(call.id, call.name, result.content))

    def push_raw_tool(self, message: ToolMessage) -> None:
        """直接入史一条工具消息（熔断善后：为悬空 call_id 补推 ok=False 结果）。"""
        self._messages.append(self._tool_entry(message.call_id, message.name, message.content))

    @staticmethod
    def _tool_entry(call_id: str, name: str, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": content,
        }

    def view(self) -> list[dict[str, Any]]:
        """生成发给 API 的完整消息序列（system + 全部历史）。"""
        return [{"role": "system", "content": self.system_prompt}, *self._messages]
