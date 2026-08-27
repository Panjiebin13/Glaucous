"""OpenAI 兼容 LLM 客户端：重试退避 + SSE 流式读取 + tool-call delta 拼装。

设计要点（概设 §3/§4.3/§4.4）：
- 只把 openai SDK 当 HTTP 通道；重试/退避/流式拼装全部自研；
- 流式增量按 tool_call delta 的 index 顺序拼装 arguments（增量片段需拼接）；
- arguments 的 JSON 解析与 schema 校验延迟到 tools.dispatch 层，
  使解析错误统一走 ToolResult(ok=False) 回喂通道，client 保持纯传输职责；
- 429/5xx/连接错误/超时 → 指数退避 + 抖动重试（最多 4 次）；
  4xx（鉴权/参数错）不重试直接抛出。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from ..config import LLMProfile

# 请求级超时（秒）：流式请求给足首 token 延迟，避免长思考被掐断
REQUEST_TIMEOUT = 120.0


@dataclass
class ToolCall:
    """一次待执行的工具调用（LLM 输出）。

    arguments 保留原始 JSON 字符串：解析延迟到 dispatch 层，
    解析失败可以带着原始文本回喂给模型自行修正（概设 §4.3）。
    """

    id: str
    name: str
    arguments: str


@dataclass
class AssistantMessage:
    """一次 LLM 响应：正文与工具调用可同时存在，也可都为空。"""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(RuntimeError):
    """LLM 请求在重试耗尽后的最终失败，由上层呈现给用户。"""


class LLMClient:
    """OpenAI 兼容客户端（仅 HTTP 通道职责）。

    重试语义（概设 §4.4）：可重试错误指数退避 + 随机抖动，最多 4 次重试；
    4xx 属于请求本身的问题（鉴权/参数），重试无意义，直接抛出。
    """

    MAX_RETRIES = 4
    BASE_DELAY = 1.0

    def __init__(self, profile: LLMProfile):
        self._profile = profile
        self._client = AsyncOpenAI(
            api_key=profile.api_key,
            base_url=profile.base_url,
            timeout=REQUEST_TIMEOUT,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        """发起一次流式对话请求。

        :param messages: OpenAI 格式消息序列（由 History.view() 生成）
        :param tools: OpenAI tools 定义（由 ToolRegistry.tool_schemas() 生成）
        :param on_text: 正文增量回调（CLI 用于实时打印，不做缓冲）
        :raises LLMError: 重试耗尽或不可重试错误
        """
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await self._chat_once(messages, tools, on_text)
            except Exception as exc:
                # 4xx（鉴权/参数错）重试无意义，直接失败；仅可重试错误进入退避
                if not _is_retryable(exc):
                    raise LLMError(f"LLM 请求失败（不可重试）：{exc}") from exc
                last_error = exc
                if attempt == self.MAX_RETRIES:
                    break
                # 指数退避 + 抖动：1s/2s/4s/8s 基数上叠加 0~1s 随机量，
                # 避免并发场景下同步重试形成请求风暴
                delay = self.BASE_DELAY * (2**attempt) + random.uniform(0, 1)
                await asyncio.sleep(delay)
        raise LLMError(f"LLM 请求失败（已重试 {self.MAX_RETRIES} 次）：{last_error}") from last_error

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None] | None,
    ) -> AssistantMessage:
        """单次流式请求 + tool-call delta 拼装。"""
        kwargs: dict[str, Any] = {
            "model": self._profile.model,
            "messages": messages,
            "temperature": self._profile.temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**kwargs)

        text_parts: list[str] = []
        # 按 index 累积 tool_call 增量：name/arguments 都是分片到达（概设 §4.3）
        tool_acc: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text_parts.append(delta.content)
                if on_text is not None:
                    on_text(delta.content)
            if delta.tool_calls:
                for piece in delta.tool_calls:
                    acc = tool_acc.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                    if piece.id:
                        acc["id"] += piece.id
                    if piece.function is not None:
                        if piece.function.name:
                            acc["name"] += piece.function.name
                        if piece.function.arguments:
                            acc["arguments"] += piece.function.arguments

        tool_calls = [
            ToolCall(id=acc["id"], name=acc["name"], arguments=acc["arguments"])
            for _, acc in sorted(tool_acc.items())
        ]
        text = "".join(text_parts)
        return AssistantMessage(text=text or None, tool_calls=tool_calls)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否可重试。

    - APIConnectionError / APITimeoutError：网络层瞬时故障，可重试；
    - APIStatusError：408 请求超时、429 限流、5xx 服务端故障可重试；
      其余 4xx（401 鉴权/400 参数）重试无意义。
    """
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 429) or exc.status_code >= 500
    return False
