"""会话管理命令单测（v1.1-M3 任务 3.6，spec §9.2）。

覆盖：/sessions 列表/搜索/切换/多命中候选、切换保护（turn_active）、
/rename、/fork（meta 替换/索引双条目/当前会话切换）、/stats 分布与过滤、
/degraded 降级路径（r2-S12）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import glaucous.cli as cli
from glaucous.commands import handle_command
from glaucous.context.history import History
from glaucous.sessions.index import SessionEntry, SessionIndex


class FakeRenderer:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.infos: list[str] = []
        self.errors: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


def make_ctx(tmp_path: Path) -> SimpleNamespace:
    """4 个新命令所需的最小 ctx（rebuild_loop 由测试打补丁，不触真实管线）。"""
    history = History.create("sp", tmp_path)
    history.push_user("首个任务：修复登录 bug")
    index = SessionIndex(path=tmp_path / "session_index.json")
    return SimpleNamespace(
        workspace=tmp_path,
        system_prompt="sp",
        history=history,
        state=SimpleNamespace(mode="build", approval_policy="auto-approve"),
        renderer=FakeRenderer(),
        session_index=index,
        session_usage={"prompt": 12, "completion": 4},
        turn_active=False,
        live_hooks={"pause": lambda: None, "resume": lambda: None, "step": lambda: None},
        stream_state={"printed": False},
        text_segment=[],
        session_events=[],
        turn_usage={"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None},
        active_agent="主 agent",
        active_task="",
        last_budget=None,
    )


def _register(idx: SessionIndex, workspace: Path, sid: str, name: str, token: int = 10) -> None:
    idx.upsert(SessionEntry(
        id=sid, name=name, workspace=str(workspace.resolve()),
        created_at="2026-08-30T10:00:00", updated_at="2026-08-30T10:00:00",
        message_count=3, token_used=token,
    ))


def _make_real_session_file(workspace: Path, sid_stem: str) -> Path:
    """按指定 stem 在用户级 project-hash 目录构造可加载的真实会话文件。"""
    from glaucous.sessions.paths import project_dir

    history = History.create("sp", workspace, session_dir=project_dir(workspace))
    path = history.session_file
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    meta["session_id"] = sid_stem
    lines[0] = json.dumps(meta, ensure_ascii=False)
    target = path.parent / f"{sid_stem}.jsonl"
    path.unlink()
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """用户级目录重定向到 tmp（避免污染真实 ~/.glaucous）。

    autouse（用户实测事故 2026-08-31 修复）：本文件多个用例未显式声明本
    fixture，_make_real_session_file 经 project_dir 把测试会话写进了真实
    ~/.glaucous/sessions/；sessions_root 与 index_path 两侧模块属性同步打补丁。"""
    home = tmp_path / "home"
    from glaucous.sessions import index as index_mod
    from glaucous.sessions import paths as spaths

    root = lambda: home / ".glaucous" / "sessions"  # noqa: E731
    ipath = lambda: home / ".glaucous" / "session_index.json"  # noqa: E731
    monkeypatch.setattr(spaths, "sessions_root", root)
    monkeypatch.setattr(spaths, "index_path", ipath)
    monkeypatch.setattr(index_mod, "sessions_root", root)
    monkeypatch.setattr(index_mod, "index_path", ipath)


@pytest.fixture()
def no_rebuild(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(cli, "rebuild_loop", lambda ctx, thinking=None: calls.append(ctx))
    return calls


# ---------------------------------------------------------------------------
# /sessions（FR-47）


class TestSessionsCommand:
    def test_empty_hint(self, tmp_path: Path, no_rebuild: list) -> None:
        ctx = make_ctx(tmp_path)
        asyncio.run(handle_command("/sessions", ctx))
        assert any("暂无会话" in n for n in ctx.renderer.notes)

    def test_switch_by_id_restores_usage(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        ctx = make_ctx(tmp_path)
        target = _make_real_session_file(tmp_path, "20260830-aaaa")
        _register(ctx.session_index, tmp_path, "20260830-aaaa", "目标会话", token=77)
        asyncio.run(handle_command("/sessions 20260830-aaaa", ctx))
        assert ctx.history.session_id == "20260830-aaaa"
        assert ctx.session_usage == {"prompt": 77, "completion": 0}  # r2-S10 恢复口径
        assert any("已切换到会话" in i for i in ctx.renderer.infos)

    def test_multi_prefix_shows_candidates_no_switch(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        ctx = make_ctx(tmp_path)
        for suffix in ("bbb", "bbc"):
            _make_real_session_file(tmp_path, f"20260830-{suffix}")
            _register(ctx.session_index, tmp_path, f"20260830-{suffix}", f"会话{suffix}")
        before = ctx.history.session_id
        asyncio.run(handle_command("/sessions 20260830-b", ctx))
        assert ctx.history.session_id == before  # 不猜测切换（r1-S2 多命中）
        assert any("候选" in n and "更长前缀" in n for n in ctx.renderer.notes)

    def test_keyword_search_and_miss(self, tmp_path: Path, no_rebuild: list) -> None:
        ctx = make_ctx(tmp_path)
        _register(ctx.session_index, tmp_path, "20260830-ccc", "支付模块排查")
        asyncio.run(handle_command("/sessions 支付", ctx))
        assert ctx.history.session_id != "20260830-ccc"  # 搜索不切换
        asyncio.run(handle_command("/sessions 不存在的关键词", ctx))
        assert any("未找到匹配会话" in n for n in ctx.renderer.notes)

    def test_exact_name_unique_switches(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        # 用户实测反馈（2026-08-30）：精确同名唯一 → 切换（子串仍仅展示）
        ctx = make_ctx(tmp_path)
        _make_real_session_file(tmp_path, "20260830-eee")
        _register(ctx.session_index, tmp_path, "20260830-eee", "最新的会话")
        _make_real_session_file(tmp_path, "20260830-fff")
        _register(ctx.session_index, tmp_path, "20260830-fff", "最新的会话-fork")
        asyncio.run(handle_command("/sessions 最新的会话-fork", ctx))  # 精确同名唯一
        assert ctx.history.session_id == "20260830-fff"

    def test_exact_name_ambiguous_lists(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        # 名称全局去重（用户实测反馈 2026-08-30）：同名 upsert 自动追加 id 尾段，
        # 列表内名称唯一 → /sessions <原名> 精确命中首个，无需再输入 id
        ctx = make_ctx(tmp_path)
        _make_real_session_file(tmp_path, "20260830-ggg")
        _register(ctx.session_index, tmp_path, "20260830-ggg", "最新的会话")
        _register(ctx.session_index, tmp_path, "20260830-hhh", "最新的会话")  # 冲突 → 追加尾段
        assert ctx.session_index.find_by_id("20260830-hhh").name == "最新的会话-hhh"
        asyncio.run(handle_command("/sessions 最新的会话", ctx))  # 精确同名唯一（hhh 已带尾段）
        assert ctx.history.session_id == "20260830-ggg"


# ---------------------------------------------------------------------------
# 切换保护（FR-50，r1-B1 生命周期）


class TestSwitchGuard:
    @pytest.mark.asyncio
    async def test_blocked_when_turn_active(self, tmp_path: Path, no_rebuild: list, monkeypatch) -> None:
        ctx = make_ctx(tmp_path)
        ctx.turn_active = True
        target = _make_real_session_file(tmp_path, "20260830-ddd")
        _register(ctx.session_index, tmp_path, "20260830-ddd", "目标")

        assert await handle_command("/sessions 20260830-ddd", ctx) is True
        assert ctx.history.session_id != "20260830-ddd"
        assert await handle_command("/fork x", ctx) is True
        called = {"resume": False}

        def _boom(*a, **k):
            called["resume"] = True

        monkeypatch.setattr(cli, "resume_history", _boom)
        await handle_command("/resume", ctx)
        assert not called["resume"]
        assert any("无法切换会话" in e for e in ctx.renderer.errors)


# ---------------------------------------------------------------------------
# /rename（FR-46）与 /fork（FR-48）


class TestRenameAndFork:
    def test_rename_updates_index(self, tmp_path: Path, no_rebuild: list) -> None:
        ctx = make_ctx(tmp_path)
        asyncio.run(handle_command("/rename 我的会话名", ctx))
        entry = ctx.session_index.find_by_id(ctx.history.session_id)
        assert entry is not None and entry.name == "我的会话名"
        # 重名重命名 → 自动追加 id 尾段（用户实测反馈：名称全局唯一）
        _register(ctx.session_index, tmp_path, "other-session", "占位")
        asyncio.run(handle_command("/rename 占位", ctx))
        renamed = ctx.session_index.find_by_id(ctx.history.session_id)
        assert renamed.name.startswith("占位-")
        asyncio.run(handle_command("/rename", ctx))
        assert any("用法" in n for n in ctx.renderer.notes)

    def test_fork_creates_independent_session(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        ctx = make_ctx(tmp_path)
        old_id = ctx.history.session_id
        old_file = ctx.history.session_file
        _register(ctx.session_index, tmp_path, old_id, "原会话")  # fork 前原会话已在索引
        old_content = old_file.read_text(encoding="utf-8")
        asyncio.run(handle_command("/fork 分叉会话", ctx))

        assert ctx.history.session_id != old_id  # 当前 REPL 切到新会话
        new_file = ctx.history.session_file
        assert new_file.is_file() and new_file != old_file
        meta = json.loads(new_file.read_text(encoding="utf-8").splitlines()[0])
        assert meta["session_id"] == new_file.stem  # meta session_id 已替换
        assert old_file.read_text(encoding="utf-8") == old_content  # 原会话未动
        assert old_file.stem == old_id
        # 索引双条目，fork 条目名 = 参数
        fork_entry = ctx.session_index.find_by_id(new_file.stem)
        assert fork_entry is not None and fork_entry.name == "分叉会话"
        assert ctx.session_index.find_by_id(old_id) is not None
        # session_usage 继承（决策 3）
        assert ctx.session_usage == {"prompt": 12, "completion": 4}


# ---------------------------------------------------------------------------
# 交付后加固（代码评审 r1-B1/B2/S5）


class TestHardening:
    def test_index_write_failure_notifies(self, tmp_path: Path) -> None:
        # r1-B1：索引写入失败经 on_error 告警（尽力而为 ≠ 静默）
        failures: list[str] = []
        fail_dir = tmp_path / "idx-is-dir"
        fail_dir.mkdir()
        idx = SessionIndex(path=fail_dir, on_error=failures.append)
        idx.upsert(SessionEntry(
            id="s1", name="n", workspace=str(tmp_path),
            created_at="t", updated_at="t", message_count=1, token_used=0,
        ))
        assert any("写入失败" in m for m in failures)

    def test_fork_io_error_keeps_session(self, tmp_path: Path, no_rebuild: list) -> None:
        # r1-B2：fork 读源失败（session_file 指向目录）→ 报错保持原会话，不击穿 REPL
        ctx = make_ctx(tmp_path)
        bogus_dir = tmp_path / "bogus-dir"
        bogus_dir.mkdir()
        ctx.history.session_file = bogus_dir
        old_id = ctx.history.session_id
        asyncio.run(handle_command("/fork x", ctx))
        assert any("无法分叉" in e for e in ctx.renderer.errors)
        assert ctx.history.session_id == old_id

    def test_fork_create_entry_error_keeps_session(self, tmp_path: Path, no_rebuild: list, monkeypatch) -> None:
        # r2-B1：fork 入口创建失败（degraded 环境 mkdir 抛出）→ 报错保持原会话
        from glaucous.sessions import paths as spaths

        def _boom(ws: Path) -> Path:
            raise OSError("read-only home")

        monkeypatch.setattr(spaths, "project_dir", _boom)
        ctx = make_ctx(tmp_path)
        old_id = ctx.history.session_id
        asyncio.run(handle_command("/fork x", ctx))
        assert any("创建分叉会话失败" in e for e in ctx.renderer.errors)
        assert ctx.history.session_id == old_id

    def test_switch_load_failure_keeps_session(self, tmp_path: Path, no_rebuild: list, fake_home: None) -> None:
        # r1-S5：索引陈旧指向已删文件 → 报错保持当前会话
        ctx = make_ctx(tmp_path)
        _register(ctx.session_index, tmp_path, "ghost-session", "幽灵会话")
        before = ctx.history.session_id
        asyncio.run(handle_command("/sessions ghost", ctx))
        assert any("切换失败" in e for e in ctx.renderer.errors)
        assert ctx.history.session_id == before

    def test_rebuild_loop_falls_back_to_ctx_thinking(self, tmp_path: Path, monkeypatch) -> None:
        # R3 回归：rebuild_loop 未显式传 thinking 时从 ctx.thinking 取（命令层均不传参）
        sentinel = object()
        captured: dict = {}
        ctx = SimpleNamespace(
            thinking=sentinel,
            workspace=tmp_path,
            config=SimpleNamespace(read_only_extra=[], max_steps=10, context_limit=1000),
            state=SimpleNamespace(),
            history=SimpleNamespace(session_id="s", messages=[]),
            audit=SimpleNamespace(),
            renderer=SimpleNamespace(),
            skills=SimpleNamespace(),
            memory_store=SimpleNamespace(),
            registry_entries={},
            current_model="m",
            llm=object(),
            outputs_dir=tmp_path,
            plans_dir=tmp_path,
            session_index=None,
        )

        def fake_make_on_event(ctx, ws, thinking):
            captured["on_event_thinking"] = thinking
            return lambda e, p: None

        monkeypatch.setattr(cli, "make_on_event", fake_make_on_event)
        monkeypatch.setattr(cli, "make_decision_callback", lambda ctx: (lambda a: None))
        monkeypatch.setattr(
            cli,
            "build_registry",
            lambda ctx, ws, thinking=None, decision_callback=None, on_event=None: (
                captured.update(registry_thinking=thinking)
                or SimpleNamespace(
                    set_approval_pipeline=lambda p: None,
                    register=lambda t: None,
                    all_tools=lambda: [],
                )
            ),
        )
        monkeypatch.setattr(cli, "ApprovalPipeline", lambda *a, **k: None)
        monkeypatch.setattr(cli, "AgentLoop", lambda *a, **k: None)

        cli.rebuild_loop(ctx)  # 不传 thinking → 应回退 ctx.thinking
        assert captured["on_event_thinking"] is sentinel
        assert captured["registry_thinking"] is sentinel


# ---------------------------------------------------------------------------
# /stats（FR-49）与 audit 过滤口径（决策 7/r2-S11）


class TestStats:
    def test_decision_distribution_filters_and_buckets(self, tmp_path: Path) -> None:
        from glaucous.sessions.stats import approval_distribution, global_totals

        audit = tmp_path / ".glaucous" / "audit.log"
        audit.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            json.dumps({"time": "t", "decision": "approve", "agent": "child-1"}),   # 审批行
            json.dumps({"time": "t", "decision": "approve", "agent": "child-1"}),   # 审批行
            json.dumps({"time": "t", "decision": "plan_mode_blocked"}),             # 无 agent → 未标注
            json.dumps({"at": "t", "event": "mode_switch"}),                        # 命令审计行 → 跳过
            "not-json-line",                                                        # 损坏行 → 跳过
        ]
        audit.write_text("\n".join(rows) + "\n", encoding="utf-8")
        dist = approval_distribution([audit])
        assert dist["approve"] == {"child-1": 2}
        assert dist["plan_mode_blocked"] == {"未标注": 1}
        assert len(dist) == 2

        index = {"version": 1, "projects": {"h": {"workspace": "w", "sessions": [
            {"message_count": 3, "token_used": 50},
            {"message_count": 2, "token_used": 30},
        ]}}}
        assert global_totals(index) == {"sessions": 2, "messages": 5, "tokens": 80}

    def test_stats_renders_without_error(self, tmp_path: Path, no_rebuild: list) -> None:
        ctx = make_ctx(tmp_path)
        audit = tmp_path / ".glaucous" / "audit.log"
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(
            json.dumps({"time": "t", "decision": "approve", "agent": "main"}) + "\n", encoding="utf-8"
        )
        asyncio.run(handle_command("/stats", ctx))
        assert ctx.renderer.errors == []


# ---------------------------------------------------------------------------
# degraded 降级路径（r1-S8/r2-S12）


class TestDegradedEntry:
    def test_create_session_history_fallback(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.sessions import paths as spaths

        def _boom(ws: Path) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(spaths, "project_dir", _boom)
        history, degraded = spaths.create_session_history("sp", tmp_path)
        assert degraded
        assert history.session_file.is_relative_to(tmp_path)  # 回退 workspace 旧路径
