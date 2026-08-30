"""会话索引与存储单测（v1.1-M3 任务 3.6，spec §9.1）。

覆盖：project-hash 稳定性、索引 upsert/touch/remove 幂等、损坏重建、
自动迁移幂等（agents/ 不动）、自动命名规则（/skill 包装行跳过、rename 优先）、
create_session_history 用户级入口与 degraded 降级路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glaucous.context.history import History
from glaucous.sessions import paths as spaths
from glaucous.sessions.index import SessionEntry, SessionIndex, derive_name, entry_from_file
from glaucous.sessions.paths import (
    create_session_history,
    index_path,
    migrate_legacy_sessions,
    project_dir,
    project_hash,
)


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """用户级目录重定向到 tmp（paths 与 index 两侧的 index_path/sessions_root 同步打补丁）。"""
    home = tmp_path / "home"
    monkeypatch.setattr(spaths, "sessions_root", lambda: home / ".glaucous" / "sessions")
    monkeypatch.setattr(spaths, "index_path", lambda: home / ".glaucous" / "session_index.json")
    import glaucous.sessions.index as index_mod

    monkeypatch.setattr(index_mod, "index_path", lambda: home / ".glaucous" / "session_index.json")
    monkeypatch.setattr(index_mod, "sessions_root", lambda: home / ".glaucous" / "sessions")
    return home


def _make_session(workspace: Path, user_text: str = "帮我修复登录 bug", user_level: bool = False) -> Path:
    """创建一个带消息的真实会话文件，返回路径（user_level=True 时落用户级目录）。"""
    history = History.create("sp", workspace, session_dir=project_dir(workspace) if user_level else None)
    history.push_user(user_text)
    return history.session_file  # type: ignore[return-value]


class TestProjectHash:
    def test_stable_and_distinct(self, tmp_path: Path) -> None:
        assert project_hash(tmp_path) == project_hash(tmp_path)
        assert project_hash(tmp_path) != project_hash(tmp_path / "sub")


class TestIndexCrud:
    def test_upsert_touch_remove(self, tmp_path: Path) -> None:
        idx = SessionIndex(path=tmp_path / "idx.json")
        entry = SessionEntry(
            id="20260830-100000-ab", name="旧名", workspace=str(tmp_path),
            created_at="2026-08-30T10:00:00", updated_at="2026-08-30T10:00:00",
            message_count=2, token_used=100,
        )
        idx.upsert(entry)
        idx.touch(entry.id, tmp_path, message_count=5, token_used=200)
        loaded = idx.find_by_id(entry.id)
        assert loaded is not None
        assert loaded.message_count == 5 and loaded.token_used == 200
        assert loaded.name == "旧名"  # touch 不带 name 不覆盖
        # auto_name 仅在 name 为空时生效（FR-46）
        idx.touch(entry.id, tmp_path, auto_name="自动名")
        assert idx.find_by_id(entry.id).name == "旧名"
        idx.touch(entry.id, tmp_path, name="新名")
        assert idx.find_by_id(entry.id).name == "新名"
        idx.remove(entry.id)
        assert idx.find_by_id(entry.id) is None

    def test_touch_creates_missing_entry(self, tmp_path: Path) -> None:
        idx = SessionIndex(path=tmp_path / "idx.json")
        idx.touch("sess-1", tmp_path, auto_name="首个任务", message_count=1, token_used=7)
        entry = idx.find_by_id("sess-1")
        assert entry is not None and entry.name == "首个任务"
        assert entry.workspace == str(tmp_path.resolve())

    def test_find_by_prefix_three_states(self, tmp_path: Path) -> None:
        idx = SessionIndex(path=tmp_path / "idx.json")
        for suffix in ("aaa", "aab"):
            idx.upsert(SessionEntry(
                id=f"20260830-{suffix}", name=f"会话{suffix}", workspace=str(tmp_path),
                created_at="2026-08-30T10:00:00", updated_at="2026-08-30T10:00:00",
                message_count=1, token_used=0,
            ))
        # 精确 stem 命中（唯一）
        assert idx.find_by_prefix("20260830-aaa", tmp_path).id == "20260830-aaa"
        # 多命中 → None（r1-S2），候选列表可得
        assert idx.find_by_prefix("20260830-a", tmp_path) is None
        assert len(idx.prefix_candidates("20260830-a", tmp_path)) == 2
        assert idx.find_by_prefix("20260830-zz", tmp_path) is None


class TestRebuild:
    def test_corrupted_index_rebuilt(self, fake_home: Path, tmp_path: Path) -> None:
        # 两个项目的真实会话文件（用户级目录，rebuild 扫描范围）
        f1 = _make_session(tmp_path, "修复登录 bug 的会话", user_level=True)
        f2 = _make_session(tmp_path / "other", "另一个项目的会话", user_level=True)
        # 手写损坏索引
        index_file = index_path()
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_text("{broken json!!", encoding="utf-8")

        idx = SessionIndex()  # 默认路径已被 fixture 补丁
        index, corrupted = idx.load()
        assert corrupted
        rebuilt = idx.rebuild(tmp_path)
        e1 = idx.find_by_id(f1.stem)
        assert e1 is not None
        assert e1.name == "修复登录 bug 的会话"  # 首条 user 消息派生（r1-S4 已知边界：自动名）
        assert e1.message_count == 1
        e2 = idx.find_by_id(f2.stem)
        assert e2 is not None
        assert e2.workspace == str((tmp_path / "other").resolve())
        assert rebuilt["version"] == 1


class TestMigration:
    def test_migration_idempotent_and_agents_untouched(
        self, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy_dir = tmp_path / ".glaucous" / "sessions"
        legacy_dir.mkdir(parents=True)
        old_session = _make_session(tmp_path)
        # 模拟 v1.0 文件在旧路径（重命名到旧目录形态）
        moved_source = legacy_dir / old_session.name
        old_session.rename(moved_source)
        agents_dir = tmp_path / ".glaucous" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "agent-1.jsonl").write_text("{}\n", encoding="utf-8")

        idx = SessionIndex()
        logs = migrate_legacy_sessions(tmp_path, idx)
        assert any("已迁移" in line for line in logs)
        dest = project_dir(tmp_path) / moved_source.name
        assert dest.is_file()
        assert not moved_source.exists()  # 原目录留空
        assert idx.find_by_id(moved_source.stem) is not None
        assert (agents_dir / "agent-1.jsonl").is_file()  # M2 子会话不迁移（spec 决策 6）

        # 幂等：二次运行为空
        assert migrate_legacy_sessions(tmp_path, idx) == []
        # 同名冲突 → 跳过移动仍 upsert（重建文件再验证）
        (legacy_dir / moved_source.name).write_text((dest).read_text(encoding="utf-8"), encoding="utf-8")
        logs2 = migrate_legacy_sessions(tmp_path, idx)
        assert any("跳过" in line for line in logs2)

    def test_no_legacy_silent(self, fake_home: Path, tmp_path: Path) -> None:
        assert migrate_legacy_sessions(tmp_path, None) == []


class TestCreateSessionHistory:
    def test_user_level_entry(self, fake_home: Path, tmp_path: Path) -> None:
        history, degraded = create_session_history("sp", tmp_path)
        assert not degraded
        assert history.session_file.is_relative_to(spaths.sessions_root())

    def test_degraded_fallback(self, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(ws: Path) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(spaths, "project_dir", _boom)
        history, degraded = create_session_history("sp", tmp_path)
        assert degraded
        # 降级回 workspace 旧路径
        assert history.session_file.is_relative_to(tmp_path)


class TestAutoName:
    def test_derive_name_skips_skill_wrapper(self) -> None:
        task = "请按照以下技能的指令执行。\n[code-review] 评审本次改动\n其他"
        assert derive_name(task) == "[code-review] 评审本次改动"[:20]
        assert derive_name("普通任务") == "普通任务"
        assert len(derive_name("超" * 50)) == 20
