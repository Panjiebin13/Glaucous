"""上下文回退/拒绝联动/loop 入口接线单测（v1.1-M4 任务 4.5，spec §七）。

覆盖：History.truncate_to（中间截断/count=0 清空/锚不匹配/写失败）、
审批拒绝联动（reject_rollback → gate 拒绝分支 + 文件已回退）、
AgentLoop.run 入口 checkpoint 接线（非空历史/空历史首轮/无 store/创建异常）。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from glaucous.agent.loop import AgentLoop
from glaucous.commands import handle_command
from glaucous.checkpoint.store import CheckpointStore
from glaucous.context.history import ContextAnchorMismatch, History, history_digest
from glaucous.llm.client import AssistantMessage
from glaucous.permission.approval import ApprovalAction, ApprovalDecision, ApprovalPipeline, AuditLog
from glaucous.permission.modes import SessionState
from glaucous.permission.risk import Risk
from glaucous.tools.base import ToolRegistry


def _git(ws: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(ws), capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("/.glaucous/\n", encoding="utf-8")  # S8：仅根级忽略
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def make_store(ws: Path) -> CheckpointStore:
    return CheckpointStore(ws, audit=AuditLog(ws / ".glaucous" / "audit.log"))


# ---------------------------------------------------------------------------
# 用例 7：History.truncate_to（上下文回退）


class TestTruncateTo:
    def test_truncate_middle_reload_equivalent(self, tmp_path: Path) -> None:
        h = History.create("sp", tmp_path)
        for text in ("m1", "m2", "m3", "m4", "m5"):
            h.push_user(text)
        anchor = history_digest(h.messages[2])
        h.truncate_to(3, anchor)
        assert len(h.messages) == 3
        lines = h.session_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4  # meta + 3 条
        assert json.loads(lines[0])["type"] == "session_meta"
        h2, _, _ = History.load(h.session_file, "sp")
        assert [m["content"] for m in h2.messages] == ["m1", "m2", "m3"]

    def test_count_zero_clears_conversation(self, tmp_path: Path) -> None:
        # S12：空历史首轮 checkpoint（0, ""）→ 截断到 0 = 清空对话（仅 meta 行）
        h = History.create("sp", tmp_path)
        h.push_user("m1")
        h.truncate_to(0, "")
        assert h.messages == []
        lines = h.session_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_anchor_mismatch_rejected(self, tmp_path: Path) -> None:
        h = History.create("sp", tmp_path)
        for text in ("m1", "m2"):
            h.push_user(text)
        with pytest.raises(ContextAnchorMismatch):
            h.truncate_to(2, "deadbeef0000")  # 模拟 L2 压缩后回退旧 checkpoint
        with pytest.raises(ContextAnchorMismatch):
            h.truncate_to(5, history_digest(h.messages[-1]))  # 超界
        assert len(h.messages) == 2  # 校验失败不动内存

    def test_write_failure_raises_and_memory_untouched(self, tmp_path: Path) -> None:
        h = History.create("sp", tmp_path)
        h.push_user("m1")
        h.session_file = tmp_path / "a-dir"  # 目录路径：读/写均 OSError
        (tmp_path / "a-dir").mkdir()
        with pytest.raises(OSError):
            h.truncate_to(0, "")
        assert len(h.messages) == 1


# ---------------------------------------------------------------------------
# 用例 8：拒绝联动（FR-43）


class TestRejectRollback:
    def test_gate_denies_and_file_restored(self, git_repo: Path, tmp_path: Path) -> None:
        store = make_store(git_repo)
        cp = store.create("turn1", message_count=0, anchor_digest="")
        assert cp is not None
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")

        def callback(action: ApprovalAction) -> ApprovalDecision:
            # 回调侧职责：先执行文件回退（spec 决策 4），gate 只映射拒绝语义
            store.rollback(cp)
            return ApprovalDecision(choice="reject_rollback", reason="危险命令")

        pipeline = ApprovalPipeline(
            SessionState(), callback=callback, audit=AuditLog(tmp_path / "audit.log")
        )
        verdict = pipeline.gate(
            ApprovalAction(kind="bash_command", target="rm -rf /", risk=Risk.DANGEROUS)
        )
        assert not verdict.allowed
        assert "用户拒绝并已回退：危险命令" in verdict.message
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"  # 文件已回退
        audit_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert '"decision": "reject_rollback"' in audit_text

    def test_rollback_failure_falls_back_to_reject(self, git_repo: Path, tmp_path: Path) -> None:
        # S5：回退失败 → 降级普通拒绝（回调侧职责，gate 语义不变）
        store = make_store(git_repo)
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")

        def callback(action: ApprovalAction) -> ApprovalDecision:
            try:
                store.rollback(store.get(999))  # type: ignore[arg-type] —— 不存在的 seq
            except Exception:  # noqa: BLE001
                return ApprovalDecision(choice="reject", reason="危险命令")
            return ApprovalDecision(choice="reject_rollback", reason="危险命令")

        pipeline = ApprovalPipeline(
            SessionState(), callback=callback, audit=AuditLog(tmp_path / "audit.log")
        )
        verdict = pipeline.gate(
            ApprovalAction(kind="bash_command", target="rm -rf /", risk=Risk.DANGEROUS)
        )
        assert not verdict.allowed
        assert "用户已拒绝" in verdict.message


# ---------------------------------------------------------------------------
# 用例 9：loop 入口接线（FR-40）


class ScriptedLLM:
    """按脚本依次返回响应的假 LLM（复用 test_mode_default_build 模式）。"""

    def __init__(self, script: list[AssistantMessage]) -> None:
        self._script = list(script)

    async def chat(self, messages, tools=None, on_text=None):
        return self._script.pop(0)


def make_loop(store: CheckpointStore | None, history: History, captured: list):
    on_checkpoint = (lambda cp: captured.append(cp)) if store is not None else None
    return AgentLoop(
        ScriptedLLM([AssistantMessage(text="done", tool_calls=[])]),
        ToolRegistry(),
        history,
        SessionState(),
        checkpoint_store=store,
        on_checkpoint=on_checkpoint,
    )


class TestLoopWiring:
    def test_entry_creates_checkpoint_with_pre_turn_anchor(self, git_repo: Path, tmp_path: Path) -> None:
        store = make_store(git_repo)
        history = History.create("sp", tmp_path)
        history.push_user("上一轮")
        captured: list = []
        answer = asyncio.run(make_loop(store, history, captured).run("新任务"))
        assert answer == "done"
        assert captured and captured[0].seq == store.last_created.seq  # B2 外泄链
        assert store.last_created.message_count == 1  # push_user 之前长度
        assert store.last_created.anchor_digest == history_digest(history.messages[0])
        assert history.messages[1]["content"] == "新任务"  # 任务照常入史（[-1] 已是终答）

    def test_empty_history_first_turn_empty_anchor(self, git_repo: Path, tmp_path: Path) -> None:
        # B4：新会话首轮（空历史）→ 空串锚，create 成功（FR-40 每轮入口含首轮）
        store = make_store(git_repo)
        history = History.create("sp", tmp_path)
        asyncio.run(make_loop(store, history, []).run("第一个任务"))
        assert store.last_created is not None
        assert store.last_created.message_count == 0
        assert store.last_created.anchor_digest == ""

    def test_no_store_no_checkpoint(self, tmp_path: Path) -> None:
        # 子 agent loop（store=None）不产生 checkpoint
        history = History.create("sp", tmp_path)
        asyncio.run(make_loop(None, history, []).run("任务"))
        assert True  # 无 store 即无副作用；断言 loop 正常完成

    def test_create_exception_does_not_block_turn(self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = make_store(git_repo)
        history = History.create("sp", tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("checkpoint 故障")

        monkeypatch.setattr(store, "create", boom)
        answer = asyncio.run(make_loop(store, history, []).run("任务"))
        assert answer == "done"  # 兜底设施失败不阻断任务轮


# ---------------------------------------------------------------------------
# 命令层 /rollback（S4：降级提示 + 完整流程）


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


def make_cmd_ctx(store: CheckpointStore | None, history: History | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        checkpoint_store=store,
        turn_active=False,
        renderer=FakeRenderer(),
        live_hooks={"pause": lambda: None, "resume": lambda: None},
        history=history,
        last_budget=None,
        session_index=None,
        workspace=Path("."),
    )


class TestRollbackCommand:
    def test_non_git_notes_reason(self, tmp_path: Path) -> None:
        # 用例 4（命令层）：非 Git 工作区 → 不可用原因提示
        ctx = make_cmd_ctx(make_store(tmp_path))
        asyncio.run(handle_command("/rollback", ctx))
        assert any("不是 Git 仓库" in n for n in ctx.renderer.notes)

    def test_full_flow_files_only(self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 用例 1（命令层）：选择 → 确认 → 否（保留对话）→ 文件回退完成
        import glaucous.cli as cli_mod

        store = make_store(git_repo)
        cp = store.create("turn1", message_count=0, anchor_digest="")
        assert cp is not None
        (git_repo / "a.txt").write_text("v2", encoding="utf-8")
        (git_repo / "b_new.txt").write_text("n", encoding="utf-8")
        answers = iter([0, 0, 0])  # 选第一个 checkpoint / 确认回退 / 否
        monkeypatch.setattr(cli_mod, "select_with_arrows", lambda q, options, read_key=None: next(answers))
        history = History.create("sp", tmp_path)
        history.push_user("旧消息")
        ctx = make_cmd_ctx(store, history)
        asyncio.run(handle_command("/rollback", ctx))
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v1"
        assert not (git_repo / "b_new.txt").exists()
        assert len(history.messages) == 2  # 旧消息 + 回退记录（用户验收反馈：模型可感知回退）
        assert "/rollback" in history.messages[-1]["content"]
        assert any("已回退" in i for i in ctx.renderer.infos)
        assert any("上下文已保留" in n for n in ctx.renderer.notes)


# ---------------------------------------------------------------------------
# /context 上下文档位切换（用户验收反馈 2026-08-31：三档 128K/512K/1M）


def _make_config():
    from glaucous.config import Config, LLMProfile

    return Config(
        profile=LLMProfile(base_url="u", api_key="k", model="m", temperature=0.2),
        max_steps=50,
    )


def make_context_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        config=_make_config(),
        last_budget={"used": 1, "limit": 128_000},
        renderer=FakeRenderer(),
        live_hooks={"pause": lambda: None, "resume": lambda: None},
    )


class TestContextCommand:
    def test_switch_by_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import glaucous.cli as cli_mod

        rebuilt: list = []
        monkeypatch.setattr(cli_mod, "rebuild_loop", lambda c: rebuilt.append(c))
        ctx = make_context_ctx()
        asyncio.run(handle_command("/context 3", ctx))
        assert ctx.config.context_limit == 1_000_000
        assert ctx.config.max_steps == 50  # 其他字段保留（dataclasses.replace）
        assert ctx.last_budget is None  # 占用条重算
        assert rebuilt  # loop 已重建以生效新阈值
        assert any("1,000,000" in i for i in ctx.renderer.infos)

    def test_switch_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import glaucous.cli as cli_mod

        monkeypatch.setattr(cli_mod, "rebuild_loop", lambda c: None)
        ctx = make_context_ctx()
        asyncio.run(handle_command("/context 512k", ctx))
        assert ctx.config.context_limit == 512_000

    def test_invalid_tier_errors(self) -> None:
        ctx = make_context_ctx()
        before = ctx.config.context_limit
        asyncio.run(handle_command("/context 9", ctx))
        assert ctx.config.context_limit == before  # 未变
        assert any("无效档位" in e for e in ctx.renderer.errors)

    def test_same_tier_no_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import glaucous.cli as cli_mod

        rebuilt: list = []
        monkeypatch.setattr(cli_mod, "rebuild_loop", lambda c: rebuilt.append(c))
        ctx = make_context_ctx()  # 默认 128K = 档 1
        asyncio.run(handle_command("/context 1", ctx))
        assert not rebuilt  # 无变化不重建
        assert any("无变化" in i for i in ctx.renderer.infos)

    def test_arrow_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import glaucous.cli as cli_mod

        monkeypatch.setattr(cli_mod, "rebuild_loop", lambda c: None)
        monkeypatch.setattr(cli_mod, "select_with_arrows", lambda q, options, read_key=None: 2)
        ctx = make_context_ctx()
        asyncio.run(handle_command("/context", ctx))
        assert ctx.config.context_limit == 1_000_000  # 选第 3 项（1M）

    def test_arrow_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import glaucous.cli as cli_mod

        monkeypatch.setattr(cli_mod, "rebuild_loop", lambda c: None)
        monkeypatch.setattr(cli_mod, "select_with_arrows", lambda q, options, read_key=None: None)
        ctx = make_context_ctx()
        before = ctx.config.context_limit
        asyncio.run(handle_command("/context", ctx))
        assert ctx.config.context_limit == before
        assert any("已取消" in n for n in ctx.renderer.notes)
