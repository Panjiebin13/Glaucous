"""checkpoint 快照/回退/淘汰/降级单测（v1.1-M4 任务 4.5，spec §七）。

fixture 在 tmp_path 内 git init + 初始提交（身份经 -c 注入不依赖全局配置）；
覆盖：快照/回退精确性（含新增文件移除）、untracked 跨轮还原、保留淘汰、
非 Git 降级、gitignored 排除、索引损坏起步、回退 GitError 报错。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from glaucous.checkpoint import git_snapshots
from glaucous.checkpoint.git_snapshots import GitError
from glaucous.checkpoint.store import CheckpointStore
from glaucous.permission.approval import AuditLog


def _git(ws: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(ws), capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _ref_exists(ws: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=str(ws), capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("/.glaucous/\n", encoding="utf-8")  # S8：仅根级忽略，不掩蔽 B2 场景
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def make_store(ws: Path, max_keep: int = 50) -> CheckpointStore:
    return CheckpointStore(ws, audit=AuditLog(ws / ".glaucous" / "audit.log"), max_keep=max_keep)


class TestSnapshotRollback:
    def test_precision_with_new_file_removal(self, git_repo: Path) -> None:
        # 用例 1：修改 a + 新建 b → 回退 → a 还原、b 移除、工作树对 HEAD 干净
        store = make_store(git_repo)
        cp = store.create("turn1", message_count=0, anchor_digest="")
        assert cp is not None
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")
        (git_repo / "b_new.txt").write_text("new", encoding="utf-8")
        preview = {c["path"]: c["status"] for c in store.preview_changes(cp)}
        assert preview["a.txt"] == "M"
        assert preview["b_new.txt"] == "A"  # B1：A 项来自 ls-files ∪ others − ref 树
        changes = store.rollback(cp)
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"
        assert not (git_repo / "b_new.txt").exists()
        assert _git(git_repo, "status", "--porcelain") == ""  # .glaucous/ 已 gitignore

    def test_cjk_filename_removed(self, git_repo: Path) -> None:
        # B1 回归：core.quotepath=off 后非 ASCII 文件名的 A 项可正常移除
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        (git_repo / "中文新增.txt").write_text("内容", encoding="utf-8")
        preview = {c["path"]: c["status"] for c in store.preview_changes(cp)}
        assert preview.get("中文新增.txt") == "A"
        store.rollback(cp)
        assert not (git_repo / "中文新增.txt").exists()

    def test_untracked_restored_across_turns(self, git_repo: Path) -> None:
        # 用例 2：决策 1 核心动机——untracked 文件的 checkpoint 时刻内容可还原
        store = make_store(git_repo)
        assert store.create("turn0", 0, "") is not None
        (git_repo / "c.txt").write_text("turn1", encoding="utf-8")  # untracked 新建
        cp2 = store.create("turn1", 0, "")
        (git_repo / "c.txt").write_text("turn2-modified", encoding="utf-8")
        (git_repo / "d.txt").write_text("new2", encoding="utf-8")
        assert cp2 is not None
        store.rollback(cp2)
        assert (git_repo / "c.txt").read_text(encoding="utf-8") == "turn1"
        assert not (git_repo / "d.txt").exists()

    def test_gitignored_not_in_scope(self, git_repo: Path) -> None:
        # 用例 5（S8）：gitignored 文件不进快照也不进 A 项——不被误删
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        (git_repo / ".gitignore").write_text(".glaucous/\nbuild/\n", encoding="utf-8")
        (git_repo / "build").mkdir()
        (git_repo / "build" / "out.bin").write_text("x", encoding="utf-8")
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")
        preview = {c["path"]: c["status"] for c in store.preview_changes(cp)}
        assert "build/out.bin" not in preview
        store.rollback(cp)
        assert (git_repo / "build" / "out.bin").read_text(encoding="utf-8") == "x"
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"


class TestRetention:
    def test_eviction_keeps_max_keep(self, git_repo: Path) -> None:
        # 用例 3：max_keep=3 建第 4 个 → 最旧 ref 消失 + 索引行移除
        store = make_store(git_repo, max_keep=3)
        for i in range(4):
            assert store.create(f"t{i}", 0, "") is not None
        assert [c.seq for c in store.list()] == [4, 3, 2]
        assert not _ref_exists(git_repo, "refs/glaucous/checkpoints/1")
        assert _ref_exists(git_repo, "refs/glaucous/checkpoints/4")


class TestDegradation:
    def test_non_git_workspace(self, tmp_path: Path) -> None:
        # 用例 4：非 Git 工作区 → 不可用提示、create 返回 None
        store = make_store(tmp_path)
        assert not store.available
        assert "不是 Git 仓库" in store.unavailable_reason()
        assert store.create("t", 0, "") is None
        assert store.list() == []

    def test_index_corruption_starts_fresh(self, git_repo: Path) -> None:
        # 用例 6a（S11）：索引损坏 → 空索引起步不崩，seq 重新计数
        store = make_store(git_repo)
        assert store.create("t1", 0, "") is not None
        (git_repo / ".glaucous" / "checkpoints.json").write_text("not json", encoding="utf-8")
        assert store.list() == []
        cp = store.create("t2", 0, "")
        assert cp is not None and cp.seq == 1
        assert len(store.list()) == 1

    def test_rollback_git_error_raises(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 用例 6b：restore 失败 → GitError 上抛（调用方报错不静默）
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")

        def boom(*args, **kwargs):
            raise GitError("模拟 git 故障")

        monkeypatch.setattr(git_snapshots, "restore_from", boom)
        with pytest.raises(GitError):
            store.rollback(cp)

    def test_workspace_subdir_excludes_runtime(self, git_repo: Path) -> None:
        # B2 回归：workspace 为仓库子目录时，sub/.glaucous 与根级 .glaucous
        # 均不进快照与 A 项（回退不删除索引/会话/审计）
        sub = git_repo / "sub"
        sub.mkdir()
        store = CheckpointStore(sub, audit=AuditLog(sub / ".glaucous" / "audit.log"))
        cp = store.create("t", 0, "")
        assert cp is not None
        (sub / "new.txt").write_text("x", encoding="utf-8")
        store.rollback(cp)
        assert (sub / ".glaucous" / "checkpoints.json").is_file()  # 索引未被回退删除
        assert not (sub / "new.txt").exists()

    def test_any_level_glaucous_excluded(self, git_repo: Path) -> None:
        # B5 回归：同仓其他实例的 sub/.glaucous 也不进快照/回退面（目录名段语义）
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        sibling = git_repo / "sub" / ".glaucous" / "sessions"
        sibling.mkdir(parents=True)
        (sibling / "s1.jsonl").write_text("{}", encoding="utf-8")
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")
        store.rollback(cp)
        assert (sibling / "s1.jsonl").is_file()  # 兄弟实例会话未被删
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"

    def test_corrupt_index_entry_not_fatal(self, git_repo: Path) -> None:
        # B4 回归：索引含错型条目（非 dict）→ create 不永久失败，条目被清理
        store = make_store(git_repo)
        assert store.create("t1", 0, "") is not None
        idx = git_repo / ".glaucous" / "checkpoints.json"
        idx.write_text(json.dumps({"version": 1, "seq": 1, "checkpoints": [1]}), encoding="utf-8")
        cp = store.create("t2", 0, "")
        assert cp is not None and cp.seq == 2
        assert [c.seq for c in store.list()] == [2]  # 错型条目被清理（t1 入口丢失，§六 1 边界）

    def test_corrupt_seq_not_fatal(self, git_repo: Path) -> None:
        # S12：seq 错型 → 归一化 0，create 成功且 seq 从 1 重新计数
        store = make_store(git_repo)
        idx = git_repo / ".glaucous" / "checkpoints.json"
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(json.dumps({"version": 1, "seq": "oops", "checkpoints": []}), encoding="utf-8")
        cp = store.create("t", 0, "")
        assert cp is not None and cp.seq == 1

    def test_deep_runtime_excluded_at_create(self, git_repo: Path) -> None:
        # B6 回归：create 前深层 .glaucous 已存在 + 根级被 ignore（fixture 即此
        # 配置）——深层运行时文件不得留在快照树（rm 两 pathspec 拆调用的动机）
        deep = git_repo / "sub" / ".glaucous"
        deep.mkdir(parents=True)
        (deep / "audit.log").write_text("log", encoding="utf-8")
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        tree = _git(git_repo, "ls-tree", "-r", "--name-only", cp.ref)
        assert ".glaucous" not in tree
        assert "sub/.glaucous/audit.log" not in tree

    def test_user_tracked_glaucous_not_rolled_back(self, git_repo: Path) -> None:
        # B7 回归：用户 tracked 的 .glaucous 文件不进回退面（保持修改后内容）
        keep = git_repo / ".glaucous" / "keep.txt"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("tracked", encoding="utf-8")
        _git(git_repo, "add", "-f", ".glaucous/keep.txt")
        _git(git_repo, "commit", "-q", "-m", "track keep")
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        keep.write_text("modified", encoding="utf-8")
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")
        store.rollback(cp)
        assert keep.read_text(encoding="utf-8") == "modified"  # 不被还原也不被删除
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"

    def test_a_item_unlink_failure_reported(self, git_repo: Path) -> None:
        # 用例 6b（S4）：A 项 unlink 失败 → failed 标记（目录只读）
        store = make_store(git_repo)
        cp = store.create("t", 0, "")
        assert cp is not None
        locked = git_repo / "locked"
        locked.mkdir()
        (locked / "f.txt").write_text("x", encoding="utf-8")
        import os

        os.chmod(locked, 0o555)
        try:
            changes = store.rollback(cp)
        finally:
            os.chmod(locked, 0o755)
        failed = [c["path"] for c in changes if c.get("failed")]
        assert "locked/f.txt" in failed

    def test_create_failure_audited_and_warns_once(self, tmp_path: Path) -> None:
        # B3：非 Git 早退分支审计 ok=false + take_warning 一次性告警
        store = make_store(tmp_path)
        assert store.create("t", 0, "") is None
        assert '"ok": false' in (tmp_path / ".glaucous" / "audit.log").read_text(encoding="utf-8")
        warn1 = store.take_warning()
        warn2 = store.take_warning()
        assert warn1 is not None and "不是 Git 仓库" in warn1
        assert warn2 is None  # 一次性
