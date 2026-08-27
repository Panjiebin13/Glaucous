"""运行配置：从环境变量加载 LLM 档案与全局配置。

Day 1 采用环境变量单模型方案（models.toml 注册表是 M3.4 任务，
环境变量兜底也是开发计划表风险预案的裁剪方向）。

环境变量约定（概设 §9）：
- GLAUCOUS_BASE_URL      OpenAI 兼容网关地址，默认 https://api.deepseek.com/v1
- GLAUCOUS_API_KEY       API 密钥，缺失时启动即报错退出（凭据只经环境变量提供，绝不入库）
- GLAUCOUS_MODEL         模型名，默认 deepseek-chat
- GLAUCOUS_TEMPERATURE   采样温度，默认 0.2

M3.4 迁移说明：models.toml 注册表落地后由注册表接管模型路由，
GLAUCOUS_DEFAULT_MODEL 作为注册表的默认档案选择项与本模块的单模型环境变量形成映射。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.2

# 主循环步数上限（概设 §4.1 终止条件②：防死循环的硬熔断，默认 50 步可配）
DEFAULT_MAX_STEPS = 50


class ConfigError(RuntimeError):
    """配置缺失或非法，启动阶段即失败——绝不带病运行。"""


@dataclass(frozen=True)
class LLMProfile:
    """单个 LLM 档案。Day 1 仅一个档案；M3.4 扩展为多档案注册表。"""

    base_url: str
    api_key: str
    model: str
    temperature: float


@dataclass(frozen=True)
class Config:
    """全局配置聚合根。"""

    profile: LLMProfile
    max_steps: int


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
    return Config(profile=profile, max_steps=max_steps)
