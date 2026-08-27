"""工具基座：Tool 协议、ToolResult、ToolRegistry 与轻量 schema 校验。

设计要点（概设 §5.6/§4.3）：
- Tool 协议 = name + description + JSON Schema parameters + async execute；
- schema 校验自研轻量子集（type/required/enum/properties/minimum），
  不引入 jsonschema 依赖——保持「工具定义与本地执行自研」的约束边界；
- dispatch 是统一错误收口：工具不存在/JSON 非法/校验失败/执行异常
  四类错误全部转为 ok=False 的 ToolResult 回喂，模型自行修正；
- 解析失败熔断：全局连续解析失败计数（成功或非解析类错误清零），
  连续第 3 次抛 ParseCircuitBroken——模型修正重发携带新 call_id，
  故不能按 call_id 计数（Plan §4.1）；
- ToolResult.metadata 在执行时顺手记账，为 M2 的 L1 裁剪派生摘要
  预埋零成本数据源（概设 §4.2「执行时记账，裁剪时派生」）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..llm.client import ToolCall


class ParseCircuitBroken(RuntimeError):
    """解析失败熔断：全局连续第 3 次解析失败时抛出，由主循环捕获终止。"""


@dataclass
class ToolResult:
    """工具执行结果。

    :param ok: 成功与否；False 时 content 为可回喂给模型的错误描述
    :param content: 正文（成功为工具输出，失败为错误信息）
    :param metadata: 执行时记账的结构化元数据（概设 §4.2），
        L1 裁剪时本地拼接为「⎿ edit_file src/x.py · 成功 · 120ms · 24 行」式摘要
    """

    ok: bool
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool:
    """工具协议：子类实现 name/description/parameters 与 execute。

    parameters 为 JSON Schema dict（type/required/enum/properties/minimum 子集），
    注册表据此生成 OpenAI tools 声明并在 dispatch 时做参数校验。
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    async def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 轻量 schema 校验（自研，覆盖工具参数声明的子集）
# ---------------------------------------------------------------------------

_TYPE_CHECKERS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """按声明式 schema 校验参数，返回错误列表（空列表 = 通过）。

    支持子集：type / required / enum / properties / minimum。
    错误信息指明具体字段，便于模型按错误信息自行修正（概设 §4.3）。
    """
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in args:
            errors.append(f"缺少必填参数: {key}")
    properties = schema.get("properties", {})
    for key, value in args.items():
        if key not in properties:
            # 未声明参数不做硬拒绝：宽容转发，由工具自身决定忽略或使用
            continue
        prop = properties[key]
        expected = prop.get("type")
        if expected and expected in _TYPE_CHECKERS and not _TYPE_CHECKERS[expected](value):
            errors.append(f"参数 {key} 类型应为 {expected}，实际为 {type(value).__name__}")
            continue
        if "enum" in prop and value not in prop["enum"]:
            errors.append(f"参数 {key} 的取值 {value!r} 不在允许范围 {prop['enum']} 内")
        minimum = prop.get("minimum")
        if minimum is not None and isinstance(value, (int, float)) and value < minimum:
            errors.append(f"参数 {key} 不能小于 {minimum}")
    return errors


# ---------------------------------------------------------------------------
# 注册表与统一 dispatch
# ---------------------------------------------------------------------------


class ToolRegistry:
    """工具注册表：声明层（tool_schemas）与执行层（dispatch）的统一入口。

    Plan 模式的「声明层隐藏写工具」（M1 任务）将在本类的 expose 能力上扩展。
    """

    # 解析失败熔断阈值：前 2 次回喂修正，连续第 3 次熔断（概设 §4.3「最多 2 轮」）
    MAX_PARSE_FAILURES = 3

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._consecutive_parse_failures = 0

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def tool_schemas(self) -> list[dict[str, Any]]:
        """生成 OpenAI tools 声明（声明层：发给 API 的工具定义）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def reset_parse_counter(self) -> None:
        """重置解析失败计数。主循环在每次 run() 入口调用——
        熔断语义限定在单任务内，避免上一任务尾部残留导致误熔断（Plan §4.1）。
        """
        self._consecutive_parse_failures = 0

    async def dispatch(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用：解析 → 校验 → 执行 → 记账，四类错误统一回喂。

        :raises ParseCircuitBroken: 连续第 3 次解析失败（JSON 非法/校验失败）时抛出
        """
        started = time.perf_counter()
        tool = self._tools.get(call.name)

        if tool is None:
            # 幻觉工具：回喂可用工具列表，模型自行改道（概设 §4.3）；
            # 非解析类错误打断「连续」语义，计数清零（Plan §4.1）
            self._consecutive_parse_failures = 0
            available = "、".join(sorted(self._tools))
            return self._failure(call, f"工具 {call.name} 不存在。可用工具：{available}")

        try:
            args = json.loads(call.arguments) if call.arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return self._parse_failure(call, f"arguments 不是合法 JSON：{exc}；原始内容：{call.arguments}")
        if not isinstance(args, dict):
            return self._parse_failure(call, f"arguments 必须是 JSON 对象，实际为 {type(args).__name__}")

        errors = validate_arguments(tool.parameters, args)
        if errors:
            detail = "；".join(errors)
            return self._parse_failure(call, f"参数校验失败：{detail}")

        try:
            result = await tool.execute(**args)
        except Exception as exc:  # noqa: BLE001 —— 执行异常必须回喂自纠而非崩溃（概设 §4.4）
            # 非解析类错误同样打断「连续」语义，计数清零（Plan §4.1）
            self._consecutive_parse_failures = 0
            return self._failure(call, f"工具 {call.name} 执行异常：{exc}")

        # 成功执行清零：解析熔断只针对「连续解析失败」的空转场景
        self._consecutive_parse_failures = 0
        duration_ms = int((time.perf_counter() - started) * 1000)
        result.metadata.update(
            {
                "tool": call.name,
                "args_brief": _brief_args(args),
                "ok": result.ok,
                "duration_ms": duration_ms,
                "lines": result.content.count("\n") + 1 if result.content else 0,
            }
        )
        return result

    # -- 内部记账辅助 ------------------------------------------------------

    def _parse_failure(self, call: ToolCall, message: str) -> ToolResult:
        """解析类失败：计数 +1，连续达到阈值抛熔断异常（由 loop 捕获做 History 善后）。"""
        self._consecutive_parse_failures += 1
        if self._consecutive_parse_failures >= self.MAX_PARSE_FAILURES:
            raise ParseCircuitBroken(
                f"连续 {self.MAX_PARSE_FAILURES} 次解析失败，触发熔断终止。最后一次错误：{message}"
            )
        return self._failure(call, message)

    def _failure(self, call: ToolCall, message: str) -> ToolResult:
        """失败结果的统一记账：与成功路径保持五字段结构一致（M2 L1 摘要派生依赖）。"""
        return ToolResult(
            ok=False,
            content=message,
            metadata={
                "tool": call.name,
                "args_brief": _brief_args_from_arguments(call.arguments),
                "ok": False,
                "duration_ms": 0,
                "lines": 0,
            },
        )


def _brief_args_from_arguments(arguments: str) -> str:
    """从原始 arguments JSON 提取参数摘要（失败路径 args 尚未解析成功）。"""
    try:
        args = json.loads(arguments) if arguments.strip() else {}
        return _brief_args(args) if isinstance(args, dict) else arguments[:80]
    except json.JSONDecodeError:
        return arguments[:80]


def _brief_args(args: dict[str, Any]) -> str:
    """生成参数摘要：优先展示 path/pattern/command 等关键字段，供 UI 工具行与 L1 摘要使用。"""
    for key in ("path", "pattern", "command", "content"):
        if key in args and isinstance(args[key], str):
            return args[key][:80]
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items()) if args else ""
