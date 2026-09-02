"""输入层：prompt_toolkit 优先，三条降级路径回退 console.input（自 cli.py 拆出）。

M3 3.3 主输入（↑↓ 历史/Ctrl+R 搜索/语义样式/斜杠补全），渲染仍走 rich；
非交互（管道/重定向）回退 console.input，保住 TODO 1.8 的 cp936 stdin 净化
路径。prompt_toolkit 相关导入全部延迟到使用点（依赖损坏不拒启动，
m3-day5 plan §4.3 降级②）；提示符类名与 theme.PT_STYLE 语义名一一对应。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from ..commands import COMMAND_META
from ..theme import PT_STYLE

# prompt_toolkit 补全的斜杠命令全集（21 个：既有 17 + M3 会话管理 4 命令）
SLASH_COMMANDS = [
    "/help", "/plan", "/build", "/compact", "/clear", "/resume", "/model",
    "/memory", "/rules", "/skills", "/skill", "/init", "/stop", "/exit",
    "/quit", "/view", "/expand", "/sessions", "/rename", "/fork", "/stats",
]

# 参数段补全注册表（v1.1 反馈 F1，取代 PATH_ARG_COMMANDS 超集）：
# /view → 工作区路径补全；/model → 模型名前缀过滤（候选经 model_names 动态取）；
# /build → 授权策略两合法参数；/skill → 技能名补全（候选经 skill_names 动态取）
ARG_COMPLETIONS = {"/view": "path", "/model": "model", "/build": "policy", "/skill": "skill", "/sessions": "session", "/spec": "specsub"}

# 路径补全的排除目录名（单层）；.glaucous/sessions 在路径层单独判定（v1.1 R2）
_PATH_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
# 单层候选上限（超大目录防卡），超出追加只读提示项（不可选中）
_PATH_MAX_CANDIDATES = 200


def _workspace_path_candidates(workspace: Path, arg: str) -> list:
    """工作区内路径候选（目录尾缀 / 便于继续深入；遍历异常静默返回空，v1.1 R2）。

    返回 (text, display, start_position) 三元组：text 为相对路径全文（供模糊匹配），
    start_position 只替换最后一段，候选插入后不丢失已输入的目录前缀。
    """
    from prompt_toolkit.completion import Completion

    arg = arg.strip()
    try:
        if arg.endswith("/") or arg == "":
            base, prefix = workspace / arg if arg else workspace, ""
        else:
            base, prefix = workspace / Path(arg).parent, Path(arg).name
        base = base.resolve()
        if workspace not in base.parents and base != workspace:
            return []
        candidates: list = []
        entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries:
            if entry.name in _PATH_EXCLUDE_DIRS:
                continue
            try:
                rel = entry.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel.startswith(".glaucous/sessions"):
                continue
            if prefix and not entry.name.lower().startswith(prefix.lower()):
                continue
            if entry.is_dir():
                candidates.append(Completion(rel + "/", start_position=-len(arg), display=entry.name + "/"))
            else:
                candidates.append(Completion(rel, start_position=-len(arg), display=entry.name))
            if len(candidates) >= _PATH_MAX_CANDIDATES:
                # 只读提示项：text 永不可匹配（用户继续输入缩小范围）
                candidates.append(Completion("\x00", display="…（更多，继续输入以缩小范围）"))
                break
        return candidates
    except Exception:  # noqa: BLE001 —— 权限/竞态等遍历异常：静默无候选，不阻断输入
        return []


def make_repl_completer(workspace: Path, model_names: Callable[[], list[str]] | None = None,
                        skill_names: Callable[[], list[str]] | None = None,
                        session_names: Callable[[], list[str]] | None = None):
    """REPL 补全器（v1.1 R2；反馈 F1 扩展 /model 参数补全与动态模型名）。

    - 命令段：/ 开头且无空格 → 命令名前缀补全（meta 取自 commands.COMMAND_META，
      单一数据源）；键入 / 立即列出全部（complete_while_typing 由 session 开启）；
    - 参数段：ARG_COMPLETIONS 注册表——/view 路径补全、/model 模型名前缀过滤
      （候选经 model_names() 动态取值：切换模型后列表跟随，不缓存快照；空格后
      无输入列全部，前缀无匹配无候选，§1.3）、/build 授权策略两合法参数、
      /skill 技能名补全（候选经 skill_names() 动态取值，技能创建后跟随刷新）；
    - 其他段（自由对话）：不弹补全；依赖导入失败返回 None（降级无补全，不拒启动）。
    """
    try:
        from prompt_toolkit.completion import Completer, Completion

        # /quit 为 /exit 别名：补全层单独登记（摘要与 /exit 同源，COMMAND_META 不重复建条目）
        meta_map = {**COMMAND_META, "/quit": COMMAND_META["/exit"]}

        class _ReplCompleter(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if text.startswith("/") and " " not in text:
                    # 命令段：前缀匹配（需求 2）；键入 / 即列全部（空串前缀命中所有）
                    for name in meta_map:
                        if name.startswith(text):
                            yield Completion(name, start_position=-len(text), display_meta=meta_map[name])
                    return
                if " " not in text:
                    return  # 自由对话段不弹补全（需求 2 边界）
                cmd, _, arg = text.partition(" ")
                kind = ARG_COMPLETIONS.get(cmd)
                if kind == "path":
                    yield from _workspace_path_candidates(workspace, arg)
                elif kind == "model":
                    for name in (model_names() if model_names else []):
                        if not arg or name.startswith(arg):
                            yield Completion(name, start_position=-len(arg), display_meta="模型档案")
                elif kind == "policy":
                    # /build 参数补全（v1.1-M1，r1-S7）：两个候选均合法，前缀过滤
                    for name in ("auto-approve", "per-action"):
                        if not arg or name.startswith(arg):
                            yield Completion(name, start_position=-len(arg), display_meta="授权策略")
                elif kind == "skill":
                    # /skill 技能名补全（v1.1 修订，用户反馈）：候选经 skill_names() 动态取值，
                    # 创建技能后跟随刷新；arg 含空格（已输入描述）时名字不再前缀匹配 → 无候选
                    for name in (skill_names() if skill_names else []):
                        if not arg or name.startswith(arg):
                            yield Completion(name, start_position=-len(arg), display_meta="技能")
                elif kind == "session":
                    # /sessions 会话名补全（v1.1-M3 简版，r1 口径）：当前项目会话名前缀过滤
                    for name in (session_names() if session_names else []):
                        if not arg or name.lower().startswith(arg.lower()):
                            yield Completion(name, start_position=-len(arg), display_meta="会话")
                elif kind == "specsub":
                    # /spec 子命令补全（v1.1-M5）；需求文本自由输入时无候选（前缀不命中）
                    for name in ("status", "cancel"):
                        if not arg or name.startswith(arg):
                            yield Completion(name, start_position=-len(arg), display_meta="Spec 管理")

        return _ReplCompleter()
    except Exception:  # noqa: BLE001 —— 补全器故障不拒启动：降级无补全输入
        return None


def make_prompt_session(workspace: Path, model_names: Callable[[], list[str]] | None = None,
                        skill_names: Callable[[], list[str]] | None = None,
                        session_names: Callable[[], list[str]] | None = None):
    """构造 prompt_toolkit PromptSession（M3-UI PT_STYLE + R2 补全 + 反馈 F1 交互）。

    降级三条件命中返回 None：① GLAUCOUS_INPUT=plain（显式开关）；② stdin
    非 TTY（测试/管道）；③ prompt_toolkit 导入/构造失败（依赖损坏不拒启动，
    m3-day5 plan §4.3）——导入在此延迟，顶层不依赖 prompt_toolkit。

    F1 两段式 Enter：补全菜单打开时 Enter 仅接受选中候选（无选中取第一条，
    候选文本落入输入行、菜单关闭），不提交执行；菜单未打开时 Enter 正常提交。
    Escape 取消补全（complete_state 清空），随后 Enter 直接提交（S1 衔接闭环）。
    默认选中第一条：候选刷新后若无选中项自动选第一候选（仅高亮，不落文本）。
    """
    if os.environ.get("GLAUCOUS_INPUT", "").strip().lower() == "plain":
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings

        (workspace / ".glaucous").mkdir(exist_ok=True)
        try:
            input_history: FileHistory | None = FileHistory(workspace / ".glaucous" / "input_history")
        except OSError:
            input_history = None

        kb = KeyBindings()

        @kb.add("enter")
        def _two_stage_enter(event) -> None:
            # 两段式（§1.2）：菜单打开时仅接受候选；候选与输入行完全一致（apply
            # 后菜单重开且无新文本可补）时视为已确认，直接提交
            buffer = event.current_buffer
            state = buffer.complete_state
            if state and state.completions:
                completion = state.current_completion or state.completions[0]
                cursor = buffer.cursor_position
                head = buffer.text[: max(0, cursor + completion.start_position)]
                tail = buffer.text[cursor:]
                if head + completion.text + tail != buffer.text:
                    buffer.apply_completion(completion)
                    return
            buffer.validate_and_handle()

        @kb.add("escape")
        def _skip_completion(event) -> None:
            event.current_buffer.cancel_completion()

        # PT_STYLE 为 None（prompt_toolkit 可导入但样式构造失败）时传默认样式；
        # complete_while_typing：键入 / 即弹命令列表（需求 2）；key_bindings：F1 两段式
        session = PromptSession(
            history=input_history,
            style=PT_STYLE,
            completer=make_repl_completer(workspace, model_names, skill_names, session_names),
            complete_while_typing=True,
            key_bindings=kb,
        )

        # 默认选中第一条（§1.1）：候选刷新后无选中项时自动选第一候选。
        # 实现（r1-B1 修复）：直接设置 complete_index = 0——①Buffer 的
        # on_completions_changed 回调实参无 completion_state 属性（读 buffer 闭包）；
        # ②complete_next() 存在候选文本落入输入行的风险，违反「仅高亮不落文本」。
        # 置位后重发 on_completions_changed 通知渲染层刷新（index 已非 None，无递归）。
        def _select_first(_event=None) -> None:
            buffer = session.default_buffer
            state = buffer.complete_state
            if state and state.completions and state.complete_index is None:
                state.complete_index = 0
                buffer.on_completions_changed()

        session.default_buffer.on_completions_changed += _select_first
        return session
    except Exception:  # noqa: BLE001 —— 输入层故障不拒启动：降级 console.input
        return None
