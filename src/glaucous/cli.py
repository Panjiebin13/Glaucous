"""简版 CLI：input 循环 + print 输出（无主题）。

Day 1 产出（计划表 0.8）：`glaucous` 命令可跑；工具调用行以
⏺/⎿ 符号语言呈现（纯文本版，M3 升级 rich 主题与状态栏）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .config import ConfigError, load_config
from .context.history import History
from .llm.client import LLMClient
from .tools.base import ToolRegistry
from .tools.files import ListDirTool, ReadFileTool
from .tools.search import GrepTool
from .ui.prompts import build_system_prompt

BANNER = "☁ Glaucous · coding agent（Day 1 原型）\n雨过天青，海鸥滑翔，代码自有清凉\n输入任务开始对话，/exit 退出。"

# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要，M3 折叠升级）
RESULT_TAIL_LINES = 3


def build_registry(workspace: Path) -> ToolRegistry:
    """装配 Day 1 的三个只读工具（bash/write 等为后续任务）。"""
    registry = ToolRegistry()
    reader = ReadFileTool(workspace)
    registry.register(reader)
    registry.register(ListDirTool(workspace, reader=reader))
    registry.register(GrepTool(workspace, reader=reader))
    return registry


def render_event(event: str, payload: dict[str, Any]) -> None:
    """loop 事件 → 纯文本渲染（⏺ 动作行 / ⎿ 结果行，学 Claude Code 的密度）。"""
    if event == "text":
        print(payload["text"], end="", flush=True)
    elif event == "diagnostic":
        # 终止诊断（步数上限/解析熔断）：loop 显式通知，保证多步轮中必达
        print(f"\n  ⎿ {payload['text']}")
    elif event == "tool_start":
        call = payload["call"]
        brief = call.arguments if len(call.arguments) <= 80 else call.arguments[:80] + "…"
        print(f"\n  ⏺ {call.name} {brief}")
    elif event == "tool_end":
        result = payload["result"]
        lines = result.content.splitlines()
        if result.ok:
            if len(lines) <= RESULT_TAIL_LINES:
                summary = " | ".join(lines) if lines else "（无输出）"
            else:
                summary = f"…共 {len(lines)} 行 | " + " | ".join(lines[-RESULT_TAIL_LINES:])
        else:
            summary = f"✘ {result.content}"
        print(f"    ⎿ {summary}")


async def repl(workspace: Path) -> None:
    """简版 REPL：读配置 → 装配 → 循环对话。"""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(BANNER)
    llm = LLMClient(config.profile)
    registry = build_registry(workspace)
    history = History(system_prompt=build_system_prompt(workspace))

    # 本轮是否有流式正文：自然终止路径终答已实时打印，仅需补换行；
    # 终止诊断路径已由 diagnostic 事件交付（自带换行），无需再补
    stream_state = {"printed": False}

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            stream_state["printed"] = True
        render_event(event, payload)

    loop = AgentLoop(llm, registry, history, max_steps=config.max_steps, on_event=on_event)

    while True:
        try:
            task = input("\n🌊 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n🌅 再见。")
            return
        if not task:
            continue
        if task in ("/exit", "/quit"):
            print("🌅 再见。")
            return
        # 自然终答已通过 on_text 流式打印（补一个收尾换行）；
        # 终止诊断已由 diagnostic 事件交付（自带换行），不再重复输出
        stream_state["printed"] = False
        try:
            answer = await loop.run(task)
        except KeyboardInterrupt:
            # Day 1 不做子进程管理（Day 2 bash 工具引入），仅中断本轮等待；
            # loop.run 内部已完成悬空 tool_call 的 History 善后，会话可继续
            print("\n（已中断本轮，可继续输入新任务）")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 顶层兜底：单轮失败不退出会话
            print(f"\n✘ 本轮执行失败：{exc}", file=sys.stderr)
            continue

        if answer and stream_state["printed"]:
            print()


def main(argv: list[str] | None = None) -> None:
    """CLI 入口：glaucous [--workspace DIR]。"""
    # ⏺/⎿/☁ 等 Unicode 符号在部分 Windows 终端（cp936 管道/重定向）下
    # 会触发 UnicodeEncodeError；errors="replace" 保证降级可读而非崩溃
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="glaucous",
        description="Glaucous —— 雨过天青，海鸥滑翔，代码自有清凉。CLI 编程智能体（Day 1 原型）。",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="工作区目录（默认当前目录）",
    )
    args = parser.parse_args(argv)
    # 统一 resolve 为绝对路径：与 prompts.py 的 resolve 基准一致，
    # 保证 grep 的 relative_to 输出与 system prompt 中的工作区信息稳定
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"工作区不存在或不是目录：{workspace}", file=sys.stderr)
        raise SystemExit(1)
    try:
        asyncio.run(repl(workspace))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
