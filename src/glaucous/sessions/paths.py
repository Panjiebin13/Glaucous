"""会话存储路径与迁移（v1.1-M3 任务 3.1，FR-44/51；spec §二/§5.4）。

布局（概设 §6.1）：
    ~/.glaucous/sessions/<project-hash>/<timestamp>-<rand>.jsonl   # 主会话
    ~/.glaucous/session_index.json                                  # 侧边索引

- project-hash：sha1(工作区绝对路径字符串)[:12]。已知边界（spec 决策 1）：
  Windows 与 WSL 访问同一项目路径形态不同 → 各环境会话独立隔离（不归一化）；
- .glaucous/agents/（M2 子 agent 会话）不迁移不索引（spec 决策 6）；
- create_session_history 是「新建主会话」的唯一入口（spec §5.4，r1-B3）：
  覆盖 repl 新会话、resume_history 三处兜底、commands._cmd_clear 共 5 处调用点。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..context.history import History

if TYPE_CHECKING:
    from .index import SessionIndex

LEGACY_SESSIONS_SUBDIR = "sessions"


def project_hash(workspace: Path) -> str:
    """项目目录哈希：sha1(工作区绝对路径字符串)[:12]（spec 决策 1）。"""
    return hashlib.sha1(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:12]


def sessions_root() -> Path:
    """用户级会话根目录：~/.glaucous/sessions。"""
    return Path.home() / ".glaucous" / "sessions"


def index_path() -> Path:
    """侧边索引文件：~/.glaucous/session_index.json。"""
    return Path.home() / ".glaucous" / "session_index.json"


def project_dir(workspace: Path) -> Path:
    """当前项目的会话目录（不存在则创建）。"""
    d = sessions_root() / project_hash(workspace)
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_session_history(system_prompt: str, workspace: Path) -> tuple[History, bool]:
    """新建主会话的唯一入口（FR-44，spec §5.4）。

    用户级 project-hash 目录创建/写入失败（OSError，极端环境）→ 降级回
    workspace 旧路径（v1.0 语义）并返回 degraded=True，调用方打一条告警，
    不阻断启动（r1-S8）。
    """
    try:
        d = project_dir(workspace)
        return History.create(system_prompt, workspace, session_dir=d), False
    except OSError:
        return History.create(system_prompt, workspace), True


def migrate_legacy_sessions(workspace: Path, index: "SessionIndex | None" = None) -> list[str]:
    """FR-51 旧会话自动迁移（幂等，启动时调用一次；spec §二）。

    扫描 <workspace>/.glaucous/sessions/*.jsonl → 移动到用户级 project-hash
    目录 → 逐个 upsert 索引（index 非 None 时）→ 返回日志行列表。
    - 同名文件已存在于目标 → 视为同一会话：跳过移动、仍 upsert 索引；
    - 原目录移动后留空（不删除目录）；无旧文件 → 返回空列表（静默）；
    - 单文件 IO 失败尽力而为（跳过并记日志），不阻断启动。
    """
    legacy = Path(workspace) / ".glaucous" / LEGACY_SESSIONS_SUBDIR
    logs: list[str] = []
    if not legacy.is_dir():
        return logs
    try:
        target = project_dir(workspace)
    except OSError as exc:
        return [f"  ⚠ 用户级会话目录不可用，跳过迁移（{exc}）"]

    # 延迟导入：index → paths（project_hash），反向引用只能函数内完成
    from .index import entry_from_file

    for f in sorted(legacy.glob("*.jsonl")):
        dest = target / f.name
        try:
            if dest.exists():
                logs.append(f"  ⚠ 跳过（目标已存在同名会话）：{f.name}")
            else:
                shutil.move(str(f), str(dest))
                logs.append(f"已迁移：{f.name}")
            entry = entry_from_file(dest)
            if entry is not None and index is not None:
                index.upsert(entry)
        except OSError as exc:
            logs.append(f"  ⚠ 迁移失败：{f.name}（{exc}）")
    return logs
