"""/view 文件查看命令（自 cli.py 拆出，v1.1 评审重构）。

按后缀分发的渲染器注册表：新增类型只需在 _VIEW_RENDERERS 加一行，
不碰 _cmd_view 主逻辑；未知类型回退提示走 read_file。
"""

from __future__ import annotations

from ..permission.workspace import Workspace, WorkspaceEscape
from ..theme import (
    console,
    escape,
    render_code_doc,
    render_csv_doc,
    render_markdown_doc,
    render_text_doc,
)

# markdown 文档卡片渲染的行数上限：read_file 打开 .md 时，内容行数 ≤ 此值才
# 渲染卡片（防长文档刷屏）；超长维持默认摘要并提示 /view 主动查看
MD_RENDER_MAX_LINES = 200

# 按后缀分发的渲染器注册表（M3 3.3 扩展：代码/文本/CSV）
_VIEW_RENDERERS: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".py": "code",
    ".pyi": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".go": "code",
    ".java": "code",
    ".rs": "code",
    ".c": "code",
    ".h": "code",
    ".cpp": "code",
    ".sh": "code",
    ".bash": "code",
    ".toml": "code",
    ".yaml": "code",
    ".yml": "code",
    ".json": "code",
    ".html": "code",
    ".css": "code",
    ".sql": "code",
    ".txt": "text",
    ".log": "text",
    ".csv": "csv",
    ".tsv": "csv",
}


def _detect_binary(data: bytes) -> bool:
    """NUL 字节检测二进制（比后缀判断可靠，防伪装后缀）。"""
    return b"\x00" in data[:8192]


def _cmd_view(path_arg: str, ws: Workspace) -> None:
    """/view <路径>：按类型渲染查看工作区内文件（沙箱校验，只读，Plan/Build 均可用）。

    类型分发：md→Markdown 卡片 / 代码→Syntax 语法高亮 / 文本→卡片原文 /
    csv→表格分列 / 未知或二进制→提示走 read_file。行数 > MD_RENDER_MAX_LINES
    时打印提示并维持摘要，不整屏刷出。
    """
    if not path_arg:
        console.print("[glaucous.warn]  /view <路径>：以渲染形式查看工作区内的文件（md/代码/文本/csv）。[/]")
        return
    try:
        target = ws.check(path_arg)
    except WorkspaceEscape as exc:
        console.print(f"[glaucous.error]  ✘ {escape(str(exc))}[/]")
        return
    if not target.exists() or not target.is_file():
        console.print(f"[glaucous.error]  ✘ 文件不存在或不是文件: {escape(str(target))}[/]")
        return
    try:
        rel = target.relative_to(ws.root)
    except ValueError:
        rel = target
    title = f":book: {rel}"

    kind = _VIEW_RENDERERS.get(target.suffix.lower())
    if kind is None:
        console.print(
            f"[glaucous.warn]  {escape(target.name)} 暂不支持渲染（{escape(target.suffix or '无后缀')}），"
            "建议用 read_file 查看原文。[/]"
        )
        return

    try:
        data = target.read_bytes()
    except OSError as exc:
        console.print(f"[glaucous.error]  ✘ 读取失败: {escape(str(exc))}[/]")
        return
    if _detect_binary(data):
        console.print(f"[glaucous.warn]  {escape(target.name)} 是二进制文件，建议用 read_file 查看原文。[/]")
        return

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        console.print(f"[glaucous.error]  ✘ 非 UTF-8 文本，无法渲染: {escape(str(exc))}[/]")
        return
    if text.count("\n") + 1 > MD_RENDER_MAX_LINES:
        console.print(f"[glaucous.muted]  （{escape(target.name)} 较长 {text.count(chr(10)) + 1} 行，未整屏渲染）[/]")
        return

    if kind == "markdown":
        render_markdown_doc(title, text)
    elif kind == "code":
        render_code_doc(title, target, text)
    elif kind == "text":
        render_text_doc(title, text)
    elif kind == "csv":
        render_csv_doc(title, text)
