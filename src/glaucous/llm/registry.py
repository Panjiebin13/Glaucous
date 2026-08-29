"""models.toml 模型注册表 + ping 连通性校验（任务 3.4，FR-26/27/33，概设 §6）。

设计要点（Day5 Plan §4.4）：
- 注册表位置 ~/.glaucous/models.toml（用户级主目录，不随仓库分发——
  天然满足「密钥不出现在任何入库文件」，FR-33）；
- 密钥零存储硬校验：段内出现 api_key 明文 → RegistryError（把约定升级为校验）；
- 环境变量兜底：文件缺失/无 [models] → 由 GLAUCOUS_* 生成名为 "env" 的单档案，
  与 config.load_profile 同默认值/同报错语义（风险预案「环境变量单模型兜底」）；
- 连通性校验只在 /model 切换时执行（D4）：最小请求（max_tokens=1，非流式），
  失败返回原因不抛错——列表路径零网络，切换失败不切换即止损。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from ..config import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_TEMPERATURE, LLMProfile

# 环境变量兜底档案名（无 models.toml 时的单档案）
ENV_PROFILE_NAME = "env"

# ping 请求超时（秒）：/model 切换的连通性校验上限
PING_TIMEOUT = 15.0


class RegistryError(RuntimeError):
    """模型注册表配置错误：启动阶段或切换阶段的显式失败，绝不带病运行。"""


@dataclass(frozen=True)
class ModelEntry:
    """toml 原始档案（密钥只存环境变量名，FR-33）。"""

    name: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float = DEFAULT_TEMPERATURE


def models_toml_path() -> Path:
    """注册表路径：~/.glaucous/models.toml（用户级，天然不入库）。"""
    return Path.home() / ".glaucous" / "models.toml"


def load_registry(env: dict[str, str] | None = None) -> tuple[dict[str, ModelEntry], str]:
    """加载注册表，返回（档案表, 默认档案名）。

    :raises RegistryError: toml 非法、字段缺失、api_key 明文、默认段名非法
    """
    source = os.environ if env is None else env
    path = models_toml_path()
    if not path.exists():
        return _env_fallback(source), ENV_PROFILE_NAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RegistryError(f"models.toml 无法解析：{exc}") from exc
    models = data.get("models")
    if not models or not isinstance(models, dict):
        return _env_fallback(source), ENV_PROFILE_NAME
    entries: dict[str, ModelEntry] = {}
    for name, spec in models.items():
        if not isinstance(spec, dict):
            raise RegistryError(f"档案 [{name}] 不是键值表")
        if "api_key" in spec:
            raise RegistryError(
                f"档案 [{name}] 含 api_key 明文：密钥只能经环境变量提供（FR-33），"
                "请改用 api_key_env 指向环境变量名"
            )
        base_url = str(spec.get("base_url", "")).strip()
        model = str(spec.get("model", "")).strip()
        api_key_env = str(spec.get("api_key_env", "")).strip()
        missing = [k for k, v in (("base_url", base_url), ("model", model), ("api_key_env", api_key_env)) if not v]
        if missing:
            raise RegistryError(f"档案 [{name}] 缺少字段：{'/'.join(missing)}")
        entries[name] = ModelEntry(
            name=name,
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            temperature=_clamp_temperature(spec.get("temperature", DEFAULT_TEMPERATURE)),
        )
    default = source.get("GLAUCOUS_DEFAULT_MODEL", "").strip() or next(iter(entries))
    if default not in entries:
        raise RegistryError(
            f"GLAUCOUS_DEFAULT_MODEL 指向未注册档案 {default!r}，可用：{'、'.join(entries)}"
        )
    return entries, default


def _env_fallback(source: dict[str, str]) -> dict[str, ModelEntry]:
    """无注册表时的环境变量单档案（与 config.load_profile 同默认值）。"""
    entry = ModelEntry(
        name=ENV_PROFILE_NAME,
        base_url=source.get("GLAUCOUS_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        model=source.get("GLAUCOUS_MODEL", "").strip() or DEFAULT_MODEL,
        api_key_env="GLAUCOUS_API_KEY",
        temperature=_clamp_temperature(source.get("GLAUCOUS_TEMPERATURE", "")),
    )
    return {ENV_PROFILE_NAME: entry}


def _clamp_temperature(raw: Any) -> float:
    """温度解析：非数字回退默认，越界钳制 [0, 2]（与 config._load_temperature 同语义）。"""
    if isinstance(raw, (int, float)):
        return max(0.0, min(2.0, float(raw)))
    text = str(raw).strip()
    if not text:
        return DEFAULT_TEMPERATURE
    try:
        return max(0.0, min(2.0, float(text)))
    except ValueError:
        return DEFAULT_TEMPERATURE


def resolve_profile(entry: ModelEntry, env: dict[str, str] | None = None) -> LLMProfile:
    """档案 → 可用 LLMProfile：api_key_env 解析环境变量取值。

    :raises RegistryError: 环境变量缺失/为空（错误信息指明变量名）
    """
    source = os.environ if env is None else env
    api_key = source.get(entry.api_key_env, "").strip()
    if not api_key:
        raise RegistryError(
            f"档案 {entry.name}：环境变量 {entry.api_key_env} 未设置。"
            f"请先设置后再使用，例如：export {entry.api_key_env}=sk-****"
        )
    return LLMProfile(
        base_url=entry.base_url,
        api_key=api_key,
        model=entry.model,
        temperature=entry.temperature,
    )


async def ping(entry: ModelEntry, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """/model 切换前的连通性校验：最小请求（max_tokens=1，非流式，≤15s）。

    返回 (成功?, 失败原因摘要)；任何异常都转为原因返回，不向调用方抛错。
    """
    source = os.environ if env is None else env
    api_key = source.get(entry.api_key_env, "").strip()
    if not api_key:
        return False, f"环境变量 {entry.api_key_env} 未设置"
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=entry.base_url, timeout=PING_TIMEOUT)
        await client.chat.completions.create(
            model=entry.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            stream=False,
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001 —— ping 的契约就是「失败给原因」
        return False, str(exc)
