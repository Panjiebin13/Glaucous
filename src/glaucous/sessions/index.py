"""侧边会话索引（v1.1-M3 任务 3.2，FR-45/46；spec §三）。

设计要点（概设 §6.1）：
- 索引文件 ~/.glaucous/session_index.json，结构与概设一致：
  {"version": 1, "projects": {"<hash>": {"workspace": str, "sessions": [entry]}}}；
- 写入时机：会话创建/自动命名/轮末刷新//rename//fork/迁移——全部尽力而为
  （原子写 tmp + replace，失败不阻断对话，r1-S8 口径）；
- 损坏重建（FR-45 降级路径）：文件缺失/JSON 损坏/结构不符 → 遍历全部
  project-hash 目录的 JSONL 派生条目（name 从首条 user 消息自动命名规则派生）；
- 会话 name 只存索引（spec 决策 2）：重建派生的是自动名，/rename 手动命名
  重建后丢失（已知边界，TODO 登记）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import index_path, project_hash, sessions_root

INDEX_VERSION = 1
AUTO_NAME_LEN = 20
# /skill 组装文本的包装行（spec §3.3 r1-S5：自动命名顺延跳过）
_SKILL_WRAPPER_PREFIX = "请按照以下技能"
_EMPTY_INDEX: dict[str, Any] = {"version": INDEX_VERSION, "projects": {}}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def derive_name(task: str) -> str:
    """自动命名（FR-46）：首个非空行前 20 字符；跳过 /skill 包装行（r1-S5）。"""
    for line in task.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(_SKILL_WRAPPER_PREFIX):
            continue
        return s[:AUTO_NAME_LEN]
    return (task.strip()[:AUTO_NAME_LEN]) or "新会话"


@dataclass
class SessionEntry:
    """单条会话摘要（字段集 = 概设 §6.1 + per-entry 冗余 workspace，r2-S13）。"""

    id: str                 # 会话文件 stem（= session_id）
    name: str               # 自动名或 /rename 覆盖值（只存索引，spec 决策 2）
    workspace: str          # 原工作区绝对路径（meta 记录）
    created_at: str
    updated_at: str
    message_count: int
    token_used: int
    status: str = "active"  # 恒 active（v1.1 无归档语义，前向兼容概设字段集，r1-S6）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionEntry":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            workspace=str(data.get("workspace", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            message_count=int(data.get("message_count", 0)),
            token_used=int(data.get("token_used", 0)),
            status=str(data.get("status", "active")),
        )


def entry_from_file(path: Path, fallback_workspace: str = "") -> SessionEntry | None:
    """从会话 JSONL 派生索引条目（迁移/重建共用；损坏文件返回 None）。"""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict) or meta.get("type") != "session_meta":
        return None

    workspace = str(meta.get("workspace") or fallback_workspace)
    message_count = 0
    name = ""
    for line in lines[1:]:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # 崩溃半行跳过（与 History.load 容错口径一致）
        if not isinstance(msg, dict) or "role" not in msg:
            continue
        message_count += 1
        if not name and msg.get("role") == "user":
            name = derive_name(str(msg.get("content") or ""))
    try:
        updated_at = datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        updated_at = _now()
    return SessionEntry(
        id=Path(path).stem,
        name=name,
        workspace=workspace,
        created_at=str(meta.get("created_at") or updated_at),
        updated_at=updated_at,
        message_count=message_count,
        token_used=0,  # 重建/迁移场景无累计记账，从 0 起算（尽力而为口径）
    )


@dataclass
class SessionIndex:
    """侧边索引读写器（spec §3.2）。path 默认惰性解析 ~/.glaucous/session_index.json
    （__post_init__ 时取值，测试可 monkeypatch index_path）。

    on_error：写入失败告警回调（r1-B1：尽力而为 ≠ 静默——OSError 时经此通道
    上报，repl 装配为 renderer.note，满足 spec §3.2/§八「失败打一条告警」）。
    """

    path: Path | None = None
    on_error: Any = None  # Callable[[str], None] | None

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = index_path()

    def _notify(self, message: str) -> None:
        if self.on_error is not None:
            try:
                self.on_error(message)
            except Exception:  # noqa: BLE001 —— 告警通道自身故障不阻断主流程
                pass

    # -- 读取与重建 ---------------------------------------------------------

    def load(self) -> tuple[dict[str, Any], bool]:
        """读取索引；(index, corrupted)。缺失/JSON 损坏/结构不符 → corrupted=True。"""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return dict(_EMPTY_INDEX), True  # 缺失视为需重建（首次启动写空骨架）
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return dict(_EMPTY_INDEX), True
        if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
            return dict(_EMPTY_INDEX), True
        return data, False

    def rebuild(self, workspace: Path) -> dict[str, Any]:
        """损坏重建（FR-45 降级路径）：遍历全部 project-hash 目录派生条目。"""
        index: dict[str, Any] = {"version": INDEX_VERSION, "projects": {}}
        root = sessions_root()
        if root.is_dir():
            for project_path in sorted(p for p in root.iterdir() if p.is_dir()):
                for f in sorted(project_path.glob("*.jsonl")):
                    entry = entry_from_file(f)
                    if entry is None:
                        continue
                    h = project_hash(Path(entry.workspace) if entry.workspace else workspace)
                    project = index["projects"].setdefault(
                        h, {"workspace": entry.workspace, "sessions": []}
                    )
                    project["sessions"].append(entry.to_dict())
        self._dedupe_index_names(index)
        self._write(index)
        return index

    # -- 写入 ---------------------------------------------------------------

    def upsert(self, entry: SessionEntry) -> None:
        """插入/更新单条（按 id 定位 project），原子写；失败尽力而为。"""
        index, corrupted = self.load()
        if corrupted:
            index = self.rebuild(Path(entry.workspace))
        h = project_hash(Path(entry.workspace) if entry.workspace else Path.cwd())
        project = index["projects"].setdefault(h, {"workspace": entry.workspace, "sessions": []})
        sessions: list[dict] = project["sessions"]
        for i, existing in enumerate(sessions):
            if existing.get("id") == entry.id:
                sessions[i] = entry.to_dict()
                break
        else:
            # 新插入：名称全局去重（用户实测反馈，同名追加 id 尾段）
            entry.name = self._dedupe_name(entry.name, entry.id)
            sessions.append(entry.to_dict())
        self._write(index)

    def _dedupe_name(self, name: str, exclude_id: str) -> str:
        """同名冲突去重（用户实测反馈 2026-08-30）：追加 id 尾段后缀。

        - 无冲突 → 原名；冲突 → f"{name}-{id 尾段}"（尾段在同项目内唯一，
          故后缀名必然唯一）；极端仍冲突 → 直接用完整 id 作名（id 全局唯一）。
        - 全局唯一（跨项目）而非项目内——/sessions <名称> 的精确消解是跨项目的。
        """
        if not name:
            return name
        others = [e for e in self.all_sessions() if e.id != exclude_id]
        if all(e.name != name for e in others):
            return name
        tail = exclude_id.rsplit("-", 1)[-1] or exclude_id[-4:]
        candidate = f"{name}-{tail}"
        if all(e.name != candidate for e in others):
            return candidate
        return exclude_id

    def _dedupe_index_names(self, index: dict[str, Any]) -> None:
        """重建后全局去重（就地修改）：先到先得保留原名，后到追加 id 尾段。"""
        used: set[str] = set()
        for project in (index.get("projects") or {}).values():
            for session in project.get("sessions", []):
                name = str(session.get("name") or "")
                if name and name not in used:
                    used.add(name)
                    continue
                sid = str(session.get("id") or "")
                tail = sid.rsplit("-", 1)[-1] or sid[-4:]
                candidate = f"{name}-{tail}" if name else sid
                session["name"] = candidate if candidate not in used else sid
                used.add(session["name"])

    def touch(
        self,
        session_id: str,
        workspace: Path,
        *,
        name: str | None = None,
        auto_name: str | None = None,
        message_count: int | None = None,
        token_used: int | None = None,
    ) -> str:
        """轮末刷新（FR-45）：updated_at=now，其余字段非 None 才覆盖；返回最终落库名。

        - name（显式，/rename）覆盖；auto_name（自动命名）仅在当前 name 为空时生效；
        - 最终名经 _dedupe_name 全局去重（用户实测反馈：同名追加 id 尾段后缀，
          列表内名称可直接用于精确切换）；
        - 会话不在索引（如重建前的活跃会话）→ 先兜底 upsert 再刷新。
        """
        ws_str = str(Path(workspace).resolve())
        index, corrupted = self.load()
        if corrupted:
            index = self.rebuild(workspace)
        h = project_hash(workspace)
        project = index["projects"].setdefault(h, {"workspace": ws_str, "sessions": []})
        sessions: list[dict] = project["sessions"]
        for existing in sessions:
            if existing.get("id") == session_id:
                final_name = str(existing.get("name") or "")
                if name is not None:
                    final_name = self._dedupe_name(name, session_id)
                    existing["name"] = final_name
                elif auto_name is not None and not final_name:
                    final_name = self._dedupe_name(auto_name, session_id)
                    existing["name"] = final_name
                if message_count is not None:
                    existing["message_count"] = message_count
                if token_used is not None:
                    existing["token_used"] = token_used
                existing["updated_at"] = _now()
                self._write(index)
                return final_name
        # 兜底：不在索引 → upsert 新条目（再应用覆盖语义）
        base_name = name if name is not None else (auto_name or "")
        entry = SessionEntry(
            id=session_id,
            name=self._dedupe_name(base_name, session_id),
            workspace=ws_str,
            created_at=_now(),
            updated_at=_now(),
            message_count=message_count if message_count is not None else 0,
            token_used=token_used if token_used is not None else 0,
        )
        sessions.append(entry.to_dict())
        self._write(index)
        return entry.name

    def remove(self, session_id: str) -> None:
        index, corrupted = self.load()
        if corrupted:
            return
        for project in index["projects"].values():
            before = len(project["sessions"])
            project["sessions"] = [s for s in project["sessions"] if s.get("id") != session_id]
            if len(project["sessions"]) != before:
                self._write(index)
                return

    # -- 查询 ---------------------------------------------------------------

    def all_sessions(self) -> list[SessionEntry]:
        """跨项目平铺（updated_at 倒序）。"""
        index, corrupted = self.load()
        if corrupted:
            index = self.rebuild(Path.cwd())
        entries = [
            SessionEntry.from_dict(s)
            for project in index["projects"].values()
            for s in project.get("sessions", [])
        ]
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def prefix_candidates(
        self, prefix: str, workspace: Path | None = None
    ) -> list[SessionEntry]:
        """id 前缀候选（r1-S2：多命中呈现用）。workspace 非 None 时先该项目内筛选。"""
        all_entries = self.all_sessions()
        if workspace is None:
            return [e for e in all_entries if e.id.startswith(prefix)]
        here = [e for e in all_entries if e.workspace == str(Path(workspace).resolve())]
        in_project = [e for e in here if e.id.startswith(prefix)]
        if in_project:
            return in_project
        return [e for e in all_entries if e.id.startswith(prefix)]

    def find_by_prefix(
        self, prefix: str, workspace: Path | None = None
    ) -> SessionEntry | None:
        """id 前缀解析（r1-S2 三态）：①项目内精确 stem → ②前缀唯一 → ③None。"""
        candidates = self.prefix_candidates(prefix, workspace)
        exact = [e for e in candidates if e.id == prefix]
        if len(exact) == 1:
            return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def search(self, keyword: str) -> list[SessionEntry]:
        """名称子串搜索（跨项目，updated_at 倒序）。"""
        return [e for e in self.all_sessions() if keyword.lower() in e.name.lower()]

    def project_names(self, workspace: Path) -> list[str]:
        """当前项目会话名列表（/sessions Tab 补全候选，r1 简版）。"""
        ws_str = str(Path(workspace).resolve())
        return [
            e.name or e.id
            for e in self.all_sessions()
            if e.workspace == ws_str
        ]

    def find_by_id(self, session_id: str) -> SessionEntry | None:
        """精确 id 查找（恢复 session_usage 用）。"""
        for e in self.all_sessions():
            if e.id == session_id:
                return e
        return None

    # -- 内部 ---------------------------------------------------------------

    def _write(self, index: dict[str, Any]) -> None:
        """原子写（tmp + replace）；IO 失败经 on_error 告警（r1-B1：不静默），不阻断。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            self._notify(f"会话索引写入失败：{exc}")
