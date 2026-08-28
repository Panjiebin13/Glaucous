"""运行配置：从环境变量加载 LLM 档案与全局配置。

环境变量约定（概设 §9）：
- GLAUCOUS_BASE_URL            OpenAI 兼容网关地址，默认 https://api.deepseek.com/v1
- GLAUCOUS_API_KEY             API 密钥，缺失时启动即报错退出（凭据只经环境变量提供，绝不入库）
- GLAUCOUS_MODEL               模型名，默认 deepseek-v4-flash
- GLAUCOUS_TEMPERATURE         采样温度，默认 0.2
- GLAUCOUS_READONLY_EXTRA      区外只读白名单路径（冒号/分号分隔），环境探测免审批（概设 §5.4）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TEMPERATURE = 0.2

# 主循环步数上限（概设 §4.1 终止条件②：防死循环的硬熔断，默认 50 步可配）
DEFAULT_MAX_STEPS = 50


class ConfigError(RuntimeError):
    """配置缺失或非法，启动阶段即失败——绝不带病运行。"""


@dataclass(frozen=True)
class LLMProfile:
    """单个 LLM 档案。M3.4 扩展为多档案注册表。"""

    base_url: str
    api_key: str
    model: str
    temperature: float


@dataclass(frozen=True)
class Config:
    """全局配置聚合根。"""

    profile: LLMProfile
    max_steps: int
    read_only_extra: tuple[Path, ...] = field(default_factory=tuple)


def load_profile(env: dict[str, str] | None = None) -> LLMProfile:
    """从环境变量加载 LLM 档案。

    :param env: 环境变量字典，缺省读 os.environ（便于测试注入）
    :raises ConfigError: API key 缺失时抛出，错误信息附设置指引
    """
    source = os.environ if env is None else env
    api_key = source.get("GLAUCOUS_API_KEY", "").strip()
    if not api_key:
        raise ConfigError(
            "未设置 GLAUCOUS_API_KEY 环境变量。"
            "请先设置后再启动，例如：export GLAUCOUS_API_KEY=sk-****"
        )
    return LLMProfile(
        base_url=source.get("GLAUCOUS_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        api_key=api_key,
        model=source.get("GLAUCOUS_MODEL", "").strip() or DEFAULT_MODEL,
        temperature=_load_temperature(source),
    )


def _load_temperature(source: dict[str, str]) -> float:
    """解析温度配置，非法值回退默认并保持启动不中断（温度非关键路径）。"""
    raw = source.get("GLAUCOUS_TEMPERATURE", "").strip()
    if not raw:
        return DEFAULT_TEMPERATURE
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TEMPERATURE
    # OpenAI 语义下温度通常在 [0, 2]；越界钳制而非报错，避免小失误阻塞使用
    return max(0.0, min(2.0, value))


def load_config(env: dict[str, str] | None = None) -> Config:
    """加载完整全局配置。"""
    profile = load_profile(env)
    source = os.environ if env is None else env
    max_steps = DEFAULT_MAX_STEPS
    raw_steps = source.get("GLAUCOUS_MAX_STEPS", "").strip()
    if raw_steps.isdigit() and int(raw_steps) > 0:
        max_steps = int(raw_steps)
    return Config(profile=profile, max_steps=max_steps, read_only_extra=_load_read_only_extra(source))


def _load_read_only_extra(source: dict[str, str]) -> tuple[Path, ...]:
    """解析区外只读白名单（GLAUCOUS_READONLY_EXTRA，冒号/分号分隔）；非法项忽略。"""
    raw = source.get("GLAUCOUS_READONLY_EXTRA", "").strip()
    if not raw:
        return ()
    paths = []
    for part in raw.replace(";", ":").split(":"):
        part = part.strip()
        if part:
            paths.append(Path(part))
    return tuple(paths)
