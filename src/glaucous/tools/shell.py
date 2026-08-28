"""bash 工具：工作区内执行 shell 命令（Day 2 任务 0.9）。

设计要点（Day2 Plan §4.2，概设 §5.6）：
- asyncio 子进程编排，cwd=工作区；Linux 一等公民，Windows 走 cmd（FR-34 基本兼容）；
- 超时：wait_for 到期 kill 进程并收尸，回喂部分输出；
- Ctrl+C（CancelledError）同样 kill 子进程后 re-raise——不留僵尸进程，
  异常由 loop 的 BaseException 善后路径接住（悬空 call_id 补推）；
- 输出 UTF-8 解码 errors="replace"（二进制输出不崩）；
- 输出防爆：合并 stdout+stderr 超过 300 行保留尾部（L0 正式策略是 M2 任务 2.5）；
- 退出码语义：非零退出码 ok=True（退出码是业务信息，模型据此定位测试失败）；
  执行过程异常（超时/无法启动）才 ok=False；
- 先全部放行：无分类器、无白名单（M1 任务 1.2/1.5 收口；期间仅可信环境使用）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult

# 输出防爆上限：超过则保留尾部（上下文防爆最小措施，M2 L0 收口）
MAX_OUTPUT_LINES = 300
# timeout 实现层钳制上限（schema minimum=1）：防模型传超大值挂死会话
MAX_TIMEOUT_SECONDS = 600
DEFAULT_TIMEOUT_SECONDS = 120
UTF8 = "utf-8"


class BashTool(Tool):
    """在工作区目录执行 shell 命令，返回退出码与输出。"""

    name = "bash"
    description = (
        "在工作区目录执行 shell 命令（如运行测试、构建、git status 等）。"
        "返回「exit_code=数字」与合并后的标准输出/错误输出；"
        "命令执行失败（非零退出码）时输出仍会完整返回，可据此定位问题。"
        "timeout 单位为秒，默认 120，最大 600。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "minimum": 1, "description": "超时秒数，默认 120，最大 600"},
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Path):
        self._workspace = workspace

    async def execute(self, command: str = "", timeout: int | None = None, **_: Any) -> ToolResult:
        if not command.strip():
            return ToolResult(ok=False, content="command 不能为空")
        # 钳制超时：模型可能传入超过上限的值，直接拒绝不如钳制到上限（保持任务可执行）
        effective_timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else min(timeout, MAX_TIMEOUT_SECONDS)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self._workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ToolResult(ok=False, content=f"无法启动子进程：{exc}")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            # 超时：kill 进程组语义（kill 主进程），再收尸取已产生的输出
            await self._kill(proc)
            partial = await self._collect_partial(proc)
            partial_text = self._decode(partial[0], partial[1])
            return ToolResult(
                ok=False,
                content=(
                    f"命令超时（{effective_timeout}s）已被终止，退出码 unknown。"
                    f"已产生的部分输出：\n{self._truncate(partial_text)}"
                ),
            )
        except asyncio.CancelledError:
            # 用户 Ctrl+C 中断本轮：kill 子进程不留僵尸，再向上抛由 loop 善后
            await self._kill(proc)
            await self._collect_partial(proc)
            raise

        output = self._decode(stdout, stderr)
        content = f"exit_code={proc.returncode}\n{self._truncate(output)}"
        return ToolResult(ok=True, content=content)

    # -- 内部辅助 ----------------------------------------------------------

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        """终止子进程；kill 失败不影响主流程（进程退出由 communicate 收尸确认）。"""
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # 进程已退出

    @staticmethod
    async def _collect_partial(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        """kill 后收尸并尽量取回已产生的输出（进程已死，communicate 立即返回）。"""
        try:
            return await proc.communicate()
        except Exception:  # noqa: BLE001 —— 收尸失败不影响错误回喂主流程
            return (b"", b"")

    @staticmethod
    def _decode(stdout: bytes, stderr: bytes) -> str:
        """合并输出并按 UTF-8 解码；二进制字节以替换符呈现（不崩，FR-34）。"""
        out = stdout.decode(UTF8, errors="replace")
        err = stderr.decode(UTF8, errors="replace")
        if out and err:
            return f"{out}\n[stderr]\n{err}"
        return out or err

    @staticmethod
    def _truncate(text: str) -> str:
        """输出防爆：超过 MAX_OUTPUT_LINES 行时保留尾部并标注（M2 L0 前的临时措施）。"""
        lines = text.splitlines()
        if len(lines) <= MAX_OUTPUT_LINES:
            return text
        kept = "\n".join(lines[-MAX_OUTPUT_LINES:])
        return f"（输出已截断，仅保留尾部 {MAX_OUTPUT_LINES} 行，共 {len(lines)} 行）\n{kept}"
