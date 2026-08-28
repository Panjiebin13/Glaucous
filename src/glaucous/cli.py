"""简版 CLI：input 循环 + print 输出（Day 3：权限管线 + 审批三选项 + 审计）。

产出（计划表 0.8~1.6）：
- 模式化提示符（🌊 plan > / 🌊 build >）；
- submit_plan 三选一交互（①每次请求权限 ②同意所有权限 ③继续讨论）；
- 审批三选项交互（per-action 时弹 [a]同意 [b]同意同类型 [c]拒绝附理由，
  破坏性命令 ⚠ 警示）——auto-approve 下 DANGEROUS/区外仍单独确认（FR-10）；
- 工作区沙箱 + 命令分类器 + 审计日志（M1 权限成型）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .agent.state import POLICY_AUTO_APPROVE, POLICY_PER_ACTION, SessionState
from .config import ConfigError, load_config
from .context.history import History
from .llm.client import LLMClient
from .permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from .permission.risk import Risk
from .permission.workspace import Workspace
from .tools.base import ToolRegistry
from .tools.files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from .tools.planning import (
    CHOICE_BUILD_AUTO_APPROVE,
    CHOICE_BUILD_PER_ACTION,
    CHOICE_KEEP_PLANNING,
    PlanDecision,
    SubmitPlanTool,
)
from .tools.search import GrepTool
from .tools.shell import BashTool
from .ui.prompts import build_system_prompt

BANNER = (
    "☁ Glaucous · coding agent（M1 权限成型）\n"
    "雨过天青，海鸥滑翔，代码自有清凉\n"
    "输入任务开始对话，/exit 退出。Plan 只读探索，Build 写操作走审批。"
)

# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要，M3 折叠升级）
RESULT_TAIL_LINES = 3

# resume 时回放的最近消息条数（仅 UI 摘要，History 本身全量加载）
RESUME_PREVIEW_MESSAGES = 6


def build_registry(
    workspace: Workspace,
    state: SessionState,
    pipeline: ApprovalPipeline,
) -> ToolRegistry:
    """装配 M1 全量工具：三个只读 + bash + 双写 + submit_plan。

    权限管线注入 registry（dispatch 层统一审批）；submit_plan 的 confirm 回调
    由 CLI 注入；工具层不感知终端（分层约定：tools 层无 UI 依赖）。
    """
    registry = ToolRegistry()
    reader = ReadFileTool(workspace)
    registry.register(reader)
    registry.register(ListDirTool(workspace, reader=reader))
    registry.register(GrepTool(workspace, reader=reader))
    registry.register(BashTool(workspace))
    registry.register(WriteFileTool(workspace, reader=reader))
    registry.register(EditFileTool(workspace, reader=reader))
    registry.set_approval_pipeline(pipeline)

    def confirm(plan: str) -> PlanDecision:
        """三选一交互：打印方案全文，读入 ①②③ 决策。

        状态切换在此接线（Day2 Plan §4.4「回调内部改 state」契约）：
        ①② 立即 enter_build——决策回喂文案「已切换 Build 模式」与实际状态
        保持一致；③ 不改状态（留在 Plan 修订方案）。
        同轮一致性由 loop 的 mode 快照保证：切换后同轮幻觉写调用仍按
        Plan 快照被 dispatch 拦截，审批不会触达，无越权窗口。
        """
        decision = prompt_plan_decision(plan)
        if decision.choice == CHOICE_BUILD_PER_ACTION:
            state.enter_build(POLICY_PER_ACTION)
        elif decision.choice == CHOICE_BUILD_AUTO_APPROVE:
            state.enter_build(POLICY_AUTO_APPROVE)
        return decision

    registry.register(SubmitPlanTool(confirm=confirm))
    return registry


def make_decision_callback():
    """审批三选项决策回调（per-action 弹三选项；auto-approve 守卫在 gate 内先行处理）。

    破坏性命令（DANGEROUS/区外写）用 ⚠ 警示 + 命令全文（M3 才 rich 主题，此处纯文本）。
    """

    def decide(action: ApprovalAction) -> ApprovalDecision:
        risk_note = {
            Risk.DANGEROUS: " ⚠ 破坏性操作（不可批量放行）",
            Risk.WRITE: "",
            Risk.SAFE: "",
        }.get(action.risk, "")
        print(f"\n  ⏺ 需要确认：{action.kind} {action.target}{risk_note}")
        if action.detail:
            # diff/说明可能多行，只展示前 60 行
            detail_lines = action.detail.splitlines()
            print("\n".join(f"    {line}" for line in detail_lines[:60]))
            if len(detail_lines) > 60:
                print(f"    …（详情共 {len(detail_lines)} 行，已截断展示）")
        dangerous = action.risk == Risk.DANGEROUS
        while True:
            try:
                if dangerous:
                    raw = input("  [a] 同意  [c] 拒绝(附理由): ").strip()
                else:
                    raw = input("  [a] 同意  [b] 同意同类型  [c] 拒绝(附理由): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ApprovalDecision(choice="reject", reason="用户中断审批")
            if raw in ("a", "A", "y", "Y"):
                return ApprovalDecision(choice="approve")
            if not dangerous and raw in ("b", "B"):
                return ApprovalDecision(choice="approve_type")
            if raw in ("c", "C", "n", "N"):
                reason = input("  拒绝理由（可留空）: ").strip() or None
                return ApprovalDecision(choice="reject", reason=reason)
            print("  无效输入，请重试。")

    return decide


def prompt_plan_decision(plan: str) -> PlanDecision:
    """打印方案全文并读取三选一决策；非法输入重问；Ctrl+C 视为③继续讨论。"""
    print("\n╭─ ◆ 方案已就绪 ──────────────────────────────")
    for line in plan.splitlines():
        print(f"│  {line}")
    print("╰──────────────────────────────────────────────")
    print("  ① 开始构建，每次请求权限")
    print("  ② 开始构建，同意所有权限")
    print("  ③ 继续讨论一下")
    while True:
        try:
            raw = input("  请选择 [1/2/3]（③可附加反馈，格式：3 反馈内容）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=None)
        if not raw:
            continue
        choice, _, feedback = raw.partition(" ")
        if choice == CHOICE_KEEP_PLANNING:
            return PlanDecision(choice=CHOICE_KEEP_PLANNING, feedback=feedback.strip() or None)
        if choice in (CHOICE_BUILD_PER_ACTION, CHOICE_BUILD_AUTO_APPROVE):
            return PlanDecision(choice=choice, feedback=feedback.strip() or None)
        print("  无效选择，请输入 1、2 或 3。")


def render_event(event: str, payload: dict[str, Any], state: SessionState) -> None:
    """loop 事件 → 纯文本渲染（⏺ 动作行 / ⎿ 结果行，学 Claude Code 的密度）。"""
    if event == "text":
        print(payload["text"], end="", flush=True)
    elif event == "diagnostic":
        # 终止诊断（步数上限/解析熔断）：loop 显式通知，保证多步轮中必达
        print(f"\n  ⎿ {payload['text']}")
    elif event == "mode_changed":
        # 模式切换/回归：提示符由 REPL 每轮按 state 重算，这里给一行可读反馈
        policy_note = (
            "·每次审批" if payload["policy"] == POLICY_PER_ACTION else "·自动放行"
        )
        print(f"  ◆ {payload['reason']}（{payload['mode']}{policy_note}）")
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


def prompt_symbol(state: SessionState) -> str:
    """模式化提示符：build 追加审批策略缩写，提醒当前授权语义。"""
    if state.mode == "build":
        policy = "每次审批" if state.approval_policy == POLICY_PER_ACTION else "auto"
        return f"🌊 build·{policy} > "
    return "🌊 plan > "


def find_latest_session(workspace: Path) -> Path | None:
    """定位工作区最新会话文件（按文件名排序取末位，命名含时间戳）。"""
    sessions_dir = workspace / ".glaucous" / "sessions"
    if not sessions_dir.is_dir():
        return None
    files = sorted(sessions_dir.glob("*.jsonl"))
    return files[-1] if files else None


def resume_history(workspace: Path, resume_id: str | None, system_prompt: str) -> tuple[History, SessionState]:
    """恢复会话：--resume 不带参数取最新；state 重置 plan/per-action（策略不跨会话持久化）。"""
    sessions_dir = workspace / ".glaucous" / "sessions"
    if resume_id == "latest" or resume_id is None:
        session_file = find_latest_session(workspace)
        if session_file is None:
            print("未找到可恢复的会话，将开始新会话。")
            return History.create(system_prompt, workspace), SessionState()
    else:
        session_file = sessions_dir / f"{resume_id}.jsonl"
        if not session_file.exists():
            # 容错：按文件名模糊匹配（用户可只输入时间戳前缀）
            candidates = [p for p in sessions_dir.glob(f"{resume_id}*.jsonl")] if sessions_dir.is_dir() else []
            if not candidates:
                print(f"未找到会话 {resume_id}，将开始新会话。")
                return History.create(system_prompt, workspace), SessionState()
            session_file = candidates[-1]

    try:
        history, meta_workspace, warnings = History.load(session_file, system_prompt)
    except (ValueError, OSError) as exc:
        print(f"会话恢复失败（{exc}），将开始新会话。")
        return History.create(system_prompt, workspace), SessionState()

    print(f"🌅 已恢复上次会话（{session_file.stem}）")
    for warning in warnings:
        print(f"  ⚠ {warning}")
    if meta_workspace and meta_workspace.resolve() != workspace:
        print(f"  ⚠ 会话记录的工作区（{meta_workspace}）与当前不一致，上下文可能错位。")
    # 恢复预览：最近几条消息摘要，帮助用户接续上下文
    recent = history.view()[-RESUME_PREVIEW_MESSAGES:]
    for entry in recent:
        role = entry.get("role", "?")
        content = entry.get("content") or "（工具调用/无文本）"
        brief = content if len(content) <= 60 else content[:60] + "…"
        print(f"  · [{role}] {brief}")
    return history, SessionState()


async def repl(workspace: Path, resume_id: str | None) -> None:
    """简版 REPL：读配置 → 装配（可恢复会话）→ 循环对话。"""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(BANNER)
    llm = LLMClient(config.profile)
    system_prompt = build_system_prompt(workspace)
    if resume_id is not None:
        history, state = resume_history(workspace, resume_id, system_prompt)
    else:
        history, state = History.create(system_prompt, workspace), SessionState()

    # M1 权限管线：沙箱 + 审批 + 审计（概设 §5）
    ws = Workspace(workspace, read_only_extra=config.read_only_extra)
    audit = AuditLog(workspace / ".glaucous" / "audit.log")
    pipeline = ApprovalPipeline(state, callback=make_decision_callback(), audit=audit)
    registry = build_registry(ws, state, pipeline)

    # 本轮是否有流式正文：自然终止路径终答已实时打印，仅需补换行；
    # 终止诊断路径已由 diagnostic 事件交付（自带换行），无需再补
    stream_state = {"printed": False}

    def on_event(event: str, payload: dict[str, Any]) -> None:
        if event == "text":
            stream_state["printed"] = True
        render_event(event, payload, state)

    loop = AgentLoop(llm, registry, history, state, max_steps=config.max_steps, on_event=on_event)

    while True:
        try:
            task = input(f"\n{prompt_symbol(state)}").strip()
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
        except (KeyboardInterrupt, asyncio.CancelledError):
            # asyncio.run 下 SIGINT 以 CancelledError 形态穿透（Day2 Plan §8）：
            # loop 已完成悬空 call 善后，中断本轮继续会话
            print("\n（已中断本轮，可继续输入新任务）")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 顶层兜底：单轮失败不退出会话
            print(f"\n✘ 本轮执行失败：{exc}", file=sys.stderr)
            continue

        if answer and stream_state["printed"]:
            print()


def main(argv: list[str] | None = None) -> None:
    """CLI 入口：glaucous [--workspace DIR] [--resume [SESSION_ID]]。"""
    # ⏺/⎿/☁ 等 Unicode 符号在部分 Windows 终端（cp936 管道/重定向）下
    # 会触发 UnicodeEncodeError；errors="replace" 保证降级可读而非崩溃
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="glaucous",
        description="Glaucous —— 雨过天青，海鸥滑翔，代码自有清凉。CLI 编程智能体（M0 原型）。",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="工作区目录（默认当前目录）",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        default=None,
        help="恢复会话：不带参数恢复最新会话，或指定会话 ID（时间戳前缀）",
    )
    args = parser.parse_args(argv)
    # 统一 resolve 为绝对路径：与 prompts.py 的 resolve 基准一致，
    # 保证 grep 的 relative_to 输出与 system prompt 中的工作区信息稳定
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"工作区不存在或不是目录：{workspace}", file=sys.stderr)
        raise SystemExit(1)
    try:
        asyncio.run(repl(workspace, args.resume))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
