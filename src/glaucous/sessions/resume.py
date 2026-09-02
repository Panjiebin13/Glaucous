"""会话恢复（启动 --resume 与 /resume 共用；自 cli.py 拆出，v1.1 评审重构）。

不带参数取最新；id 支持前缀模糊匹配；三处兜底新建统一走
create_session_history（r1-B3/r2-B4，spec §5.4）；state 重置为启动默认
（v1.1：Build + auto-approve，策略不跨会话持久化）。
"""

from __future__ import annotations

from pathlib import Path

from ..agent.state import SessionState
from ..context.history import History
from ..ui.renderer import Renderer
from .paths import create_session_history, project_dir

# resume 时回放的最近消息条数（仅 UI 摘要，History 本身全量加载）
RESUME_PREVIEW_MESSAGES = 6


def find_latest_session(workspace: Path) -> Path | None:
    """定位工作区最新会话文件（按文件名排序取末位，命名含时间戳）。

    v1.1-M3（FR-44）：用户级 project-hash 目录。
    """
    sessions_dir = project_dir(workspace)
    if not sessions_dir.is_dir():
        return None
    files = sorted(sessions_dir.glob("*.jsonl"))
    return files[-1] if files else None


def resume_history(workspace: Path, resume_id: str | None, system_prompt: str,
                   renderer: Renderer) -> tuple[History, SessionState]:
    """恢复会话：不带参数取最新；前缀模糊匹配；失败回退新会话。

    v1.1-M3（FR-44）：会话目录为用户级 project-hash 目录；三处兜底新建统一走
    create_session_history（r1-B3/r2-B4，spec §5.4）。
    state 重置为启动默认（v1.1：Build + auto-approve，策略不跨会话持久化）；恢复后 system prompt
    用传入版本（启动时构建，不重建——避免注入段闪变，Day4 D6）。
    """
    sessions_dir = project_dir(workspace)
    if resume_id == "latest" or resume_id is None:
        session_file = find_latest_session(workspace)
        if session_file is None:
            renderer.note("未找到可恢复的会话，将开始新会话。")
            history, degraded = create_session_history(system_prompt, workspace)
            if degraded:
                renderer.note("⚠ 用户级会话目录不可用，已降级到工作区旧路径。")
            return history, SessionState()
    else:
        session_file = sessions_dir / f"{resume_id}.jsonl"
        if not session_file.exists():
            # 容错：按文件名模糊匹配（用户可只输入时间戳前缀）
            candidates = [p for p in sessions_dir.glob(f"{resume_id}*.jsonl")] if sessions_dir.is_dir() else []
            if not candidates:
                renderer.note(f"未找到会话 {resume_id}，将开始新会话。")
                history, degraded = create_session_history(system_prompt, workspace)
                if degraded:
                    renderer.note("⚠ 用户级会话目录不可用，已降级到工作区旧路径。")
                return history, SessionState()
            session_file = candidates[-1]

    try:
        history, meta_workspace, warnings = History.load(session_file, system_prompt)
    except (ValueError, OSError) as exc:
        renderer.error(f"会话恢复失败（{exc}），将开始新会话。")
        history, degraded = create_session_history(system_prompt, workspace)
        if degraded:
            renderer.note("⚠ 用户级会话目录不可用，已降级到工作区旧路径。")
        return history, SessionState()

    renderer.info(f"🌅 已恢复上次会话（{session_file.stem}）")
    for warning in warnings:
        renderer.note(f"  ⚠ {warning}")
    if meta_workspace and meta_workspace.resolve() != workspace:
        renderer.note(f"  ⚠ 会话记录的工作区（{meta_workspace}）与当前不一致，上下文可能错位。")
    # 恢复预览：最近几条消息摘要，帮助用户接续上下文
    recent = history.view()[-RESUME_PREVIEW_MESSAGES:]
    for entry in recent:
        role = entry.get("role", "?")
        content = entry.get("content") or "（工具调用/无文本）"
        brief = content if len(content) <= 60 else content[:60] + "…"
        renderer.note(f"  · [{role}] {brief}")
    return history, SessionState()
