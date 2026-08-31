"""Checkpoint 存储与回退编排（v1.1-M4 任务 4.2/4.3，FR-40/41/42；spec §3.2）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..permission.approval import AuditLog
from . import git_snapshots as git

INDEX_VERSION = 1


@dataclass
class Checkpoint:
    """单个 checkpoint 的元数据（spec §3.2）；Git 侧真身在 refs/glaucous/*。"""

    seq: int
    ref: str
    commit: str
    created_at: str
    task: str
    message_count: int
    anchor_digest: str   # 与索引字段同源（空历史首轮为空串哨兵，spec B4/B5）


class CheckpointStore:
    """checkpoint 索引读写 + 快照创建 + 回退编排（spec §3.2）。

    - 索引 .glaucous/checkpoints.json：原子写（tmp + replace），损坏/缺失 →
      空索引起步（ref 残留无害，spec §六 1）；
    - available 惰性探测一次并缓存（非 Git 工作区不重复探测）；
    - 拒绝联动/回退均只回文件不动上下文（上下文由 History.truncate_to 承担）。
    """

    def __init__(self, workspace: Path, audit: AuditLog, max_keep: int = 50):
        self._workspace = Path(workspace)
        self._audit = audit
        self._max_keep = max(1, max_keep)
        self._root: Path | None = None
        self._available: bool | None = None
        self._reason = ""
        self.last_created: Checkpoint | None = None  # B2：供测试断言 / repl 消费
        self._pending_warning: str | None = None  # B3：首次创建失败的一次性告警

    # -- 可用性（惰性探测一次，spec §五） -----------------------------------

    @property
    def available(self) -> bool:
        if self._available is None:
            self._probe()
        return self._available

    def unavailable_reason(self) -> str:
        """不可用原因（available=True 时返回空串）。"""
        if not self.available:
            return self._reason
        return ""

    def _probe(self) -> None:
        if not git.is_git_workspace(self._workspace):
            self._available = False
            self._reason = "当前工作区不是 Git 仓库，checkpoint 不可用"
            return
        try:
            self._root = git.repo_root(self._workspace)
            self._available = True
        except git.GitError as exc:
            self._available = False
            self._reason = f"checkpoint 不可用：{exc}"

    def take_warning(self) -> str | None:
        """B3（S2 口径）：首次创建尝试失败时返回一次性告警文案，之后返回 None。

        FR-40「明确提示不可用原因」且不每轮打扰；loop.run 在 create 失败后
        取用并经 on_event 呈现。
        """
        warn, self._pending_warning = self._pending_warning, None
        return warn

    def _excludes(self) -> tuple[str, ...]:
        """快照/回退排除的目录名（B5 裁决：任意层级语义）。glaucous 运行时
        目录固定为 .glaucous，无论出现在仓库哪个层级（根级/子目录工作区/
        同仓其他实例）都不进快照与回退面——决策 5「审计失真」防护。"""
        return (".glaucous",)

    # -- 索引（原子写；损坏 → 空索引起步，spec §3.2） ------------------------

    @property
    def _index_path(self) -> Path:
        return self._workspace / ".glaucous" / "checkpoints.json"

    def _load_index(self) -> dict[str, Any]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("checkpoints"), list):
                try:
                    data["seq"] = int(data.get("seq") or 0)  # S12：seq 错型归一化，防 create 永久失败
                except (TypeError, ValueError):
                    data["seq"] = 0
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": INDEX_VERSION, "seq": 0, "checkpoints": []}

    def _save_index(self, index: dict[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._index_path)

    @staticmethod
    def _dump(cp: Checkpoint) -> dict[str, Any]:
        return {
            "seq": cp.seq,
            "ref": cp.ref,
            "commit": cp.commit,
            "created_at": cp.created_at,
            "task": cp.task,
            "message_count": cp.message_count,
            "anchor_digest": cp.anchor_digest,
        }

    @staticmethod
    def _load_cp(entry: Any) -> Checkpoint | None:
        """索引条目 → Checkpoint；形状不符跳过（S3：损坏条目不致命）。"""
        if not isinstance(entry, dict):
            return None
        try:
            return Checkpoint(
                seq=int(entry.get("seq", 0)),
                ref=str(entry.get("ref", "")),
                commit=str(entry.get("commit", "")),
                created_at=str(entry.get("created_at", "")),
                task=str(entry.get("task", "")),
                message_count=int(entry.get("message_count", 0)),
                anchor_digest=str(entry.get("anchor_digest", "")),
            )
        except (TypeError, ValueError):
            return None

    # -- 创建 / 保留淘汰（FR-40/41） -----------------------------------------

    def create(self, task: str, message_count: int, anchor_digest: str = "") -> Checkpoint | None:
        """每轮任务入口快照；失败返回 None（审计 ok=false + 一次性告警，B3/S2）。"""
        if not self.available:
            # B3：非 Git 早退分支同样审计留痕（此前静默返回，违反 §3.2）
            self._audit.record(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "event": "checkpoint_create",
                    "ok": False,
                    "error": self._reason,
                }
            )
            if self._pending_warning is None:
                self._pending_warning = self._reason
            return None
        assert self._root is not None
        excludes = self._excludes()
        try:
            commit = git.create_snapshot(self._root, f"glaucous-checkpoint: {task[:80]}", excludes)
            index = self._load_index()
            seq = int(index.get("seq", 0)) + 1
            ref = f"refs/glaucous/checkpoints/{seq}"
            git.update_ref(self._root, ref, commit)
            cp = Checkpoint(
                seq=seq,
                ref=ref,
                commit=commit,
                created_at=datetime.now().isoformat(timespec="seconds"),
                task=task[:100],
                message_count=message_count,
                anchor_digest=anchor_digest,
            )
            index["version"] = INDEX_VERSION
            index["seq"] = seq
            clean: list[dict[str, Any]] = []
            for e in index["checkpoints"]:
                parsed = self._load_cp(e)  # B4：先解析后 dump，错型条目跳过而非崩溃
                if parsed is not None:
                    clean.append(self._dump(parsed))
            index["checkpoints"] = clean
            index["checkpoints"].append(self._dump(cp))
            self._save_index(index)
            self._evict(index)
            self.last_created = cp
            self._audit.record(
                {
                    "time": cp.created_at,
                    "event": "checkpoint_create",
                    "ok": True,
                    "seq": seq,
                    "commit": commit,
                }
            )
            return cp
        except Exception as exc:  # noqa: BLE001 —— S2：失败返回 None 是本方法契约
            self._audit.record(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "event": "checkpoint_create",
                    "ok": False,
                    "error": str(exc),
                }
            )
            if self._pending_warning is None:
                self._pending_warning = f"checkpoint 创建失败：{exc}"
            return None

    def _evict(self, index: dict[str, Any]) -> None:
        """超 max_keep 删最旧 ref + 清索引行（验收：第 51 个创建后最旧被淘汰）。"""
        assert self._root is not None
        evicted = False
        while len(index["checkpoints"]) > self._max_keep:
            oldest = index["checkpoints"].pop(0)
            if oldest is not None and oldest.get("ref"):
                try:
                    git.delete_ref(self._root, str(oldest["ref"]))
                except git.GitError:
                    pass  # ref 已删/仓库损坏：索引行照清，不阻断创建
            evicted = True
        if evicted:
            self._save_index(index)

    # -- 查询 / 回退（FR-42） -------------------------------------------------

    def list(self) -> list[Checkpoint]:
        """保留期内 checkpoints，新→旧（S3：损坏条目过滤）。"""
        entries = [e for e in self._load_index()["checkpoints"] if isinstance(e, dict)]
        cps = [cp for e in entries if (cp := self._load_cp(e)) is not None]
        return sorted(cps, key=lambda c: c.seq, reverse=True)

    def get(self, seq: int) -> Checkpoint | None:
        for cp in self.list():
            if cp.seq == seq:
                return cp
        return None

    def preview_changes(self, cp: Checkpoint) -> list[dict]:
        """回退前变更清单预览（确认卡数据源；spec §3.4 步骤 4）。"""
        if not self.available:
            raise git.GitError(self.unavailable_reason())
        assert self._root is not None
        return git.diff_against(self._root, cp.ref, self._excludes())

    def rollback(self, cp: Checkpoint) -> list[dict]:
        """文件回退：restore M/D 项 + 移除 A 项 + 审计；返回变更清单（含 failed 标记）。"""
        if not self.available:
            raise git.GitError(self.unavailable_reason())
        assert self._root is not None
        excludes = self._excludes()
        changes = git.diff_against(self._root, cp.ref, excludes)
        git.restore_from(self._root, cp.ref, excludes)
        failed: set[str] = set()
        for item in changes:
            if item["status"] != "A":
                continue
            target = self._root / item["path"]
            try:
                if target.is_file():
                    target.unlink()
            except OSError:
                failed.add(item["path"])  # 逐文件容忍（spec §五）
        for item in changes:
            item["failed"] = item["path"] in failed
        self._audit.record(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "event": "rollback",
                "seq": cp.seq,
                "changes": len(changes),
                "failed_remove": len(failed),
            }
        )
        return changes
