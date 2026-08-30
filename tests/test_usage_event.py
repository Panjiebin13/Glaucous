"""R5 用量采集单测（v1.1 打磨 §5.1）：usage 归一化 + stream_options 降级重试。

覆盖：DeepSeek 缓存字段归一化 / OpenAI prompt_tokens_details 归一化 / 无 usage
不回调；stream_options 不被支持（不可重试类错误）→ 去参原样重试一次成功。
mock 流式 chunk（choices/usage 均为 SimpleNamespace），不起网络。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from glaucous.config import LLMProfile
from glaucous.llm.client import LLMClient, _normalize_usage


def make_profile() -> LLMProfile:
    return LLMProfile(base_url="https://example.invalid", api_key="sk-test", model="m", temperature=0.7)


class FakeStream:
    """最小异步流：按序产出 chunk。"""

    def __init__(self, chunks: list):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


def content_chunk(text: str):
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def usage_chunk(usage) -> SimpleNamespace:
    return SimpleNamespace(choices=[], usage=usage)


def install_stream(client: LLMClient, chunks: list) -> AsyncMock:
    create = AsyncMock(return_value=FakeStream(chunks))
    client._client.chat.completions.create = create
    return create


@pytest.mark.asyncio
async def test_deepseek_usage_normalized():
    """DeepSeek 风格：prompt_cache_hit/miss_tokens 直接映射。"""
    received: list[dict] = []
    client = LLMClient(make_profile(), on_usage=received.append)
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_cache_hit_tokens=800,
        prompt_cache_miss_tokens=200,
        prompt_tokens_details=None,
    )
    install_stream(client, [content_chunk("hi"), usage_chunk(usage)])
    result = await client.chat([{"role": "user", "content": "你好"}])
    assert result.text == "hi"
    assert received == [
        {"prompt": 1000, "completion": 200, "cache_hit": 800, "cache_miss": 200}
    ]


@pytest.mark.asyncio
async def test_openai_details_usage_normalized():
    """OpenAI 风格：无 DeepSeek 字段时取 prompt_tokens_details.cached_tokens。"""
    received: list[dict] = []
    client = LLMClient(make_profile(), on_usage=received.append)
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600),
    )
    install_stream(client, [content_chunk("ok"), usage_chunk(usage)])
    await client.chat([{"role": "user", "content": "hi"}])
    assert received == [
        {"prompt": 1000, "completion": 50, "cache_hit": 600, "cache_miss": 400}
    ]


@pytest.mark.asyncio
async def test_no_usage_chunk_no_callback():
    """chunk.usage 为 None 的既有流：不回调（现状不受影响）。"""
    received: list[dict] = []
    client = LLMClient(make_profile(), on_usage=received.append)
    install_stream(client, [content_chunk("plain")])
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.text == "plain"
    assert received == []


def test_normalize_usage_missing_fields_none():
    """供应商字段缺失一律 None（上层据此省略缓存段，§5.3）。"""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    assert _normalize_usage(usage) == {
        "prompt": 10, "completion": 5, "cache_hit": None, "cache_miss": None,
    }


@pytest.mark.asyncio
async def test_stream_options_degradation_retry():
    """首次因 stream_options 失败（不可重试类）→ 去参原样重试一次成功（S6）。"""
    received: list[dict] = []
    client = LLMClient(make_profile(), on_usage=received.append)
    create = AsyncMock(
        side_effect=[ValueError("stream_options not supported"), FakeStream([content_chunk("ok")])]
    )
    client._client.chat.completions.create = create
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.text == "ok"
    assert create.call_count == 2
    # 首次携带 stream_options，重试去掉该参数、其余原样
    assert create.call_args_list[0].kwargs.get("stream_options") == {"include_usage": True}
    assert "stream_options" not in create.call_args_list[1].kwargs
    assert create.call_args_list[1].kwargs["model"] == "m"
