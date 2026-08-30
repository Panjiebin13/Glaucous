"""受保护范围收窄 + 技能即时生效单测（v1.1 修订，用户 WSL 实测反馈）。

背景：`.glaucous/` 原为整体写硬拦截（Day3 §4.6 S4，防篡改审计/会话），一刀切
把 create-skill 规范目标 `.glaucous/skills/` 也拦了（submit_plan 获批后仍被拒）。
修订：保护范围收窄——skills/ 开放写，其余运行期数据（审计/会话/记忆/方案锚/
outputs/根下新文件）仍硬拦截；技能资产写入后自动刷新索引（无需重启会话）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glaucous.permission.classifier import CommandClassifier
from glaucous.permission.risk import Risk
from glaucous.permission.workspace import Workspace
from glaucous.tools.files import EditFileTool, WriteFileTool


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root)


class TestProtectedScope:
    def test_runtime_data_still_protected(self, ws: Workspace) -> None:
        # 审计/会话/记忆/方案锚/输出落盘/根下新文件：一律硬拦截（不变）
        assert ws.is_protected(ws.resolve(".glaucous/audit.log"))
        assert ws.is_protected(ws.resolve(".glaucous/sessions/20260830-120000.jsonl"))
        assert ws.is_protected(ws.resolve(".glaucous/memory.json"))
        assert ws.is_protected(ws.resolve(".glaucous/plans/20260830-abc.md"))
        assert ws.is_protected(ws.resolve(".glaucous/outputs/call-1.txt"))
        assert ws.is_protected(ws.resolve(".glaucous/unknown-new-file"))  # 未来系统文件默认保护

    def test_skills_dir_writable(self, ws: Workspace) -> None:
        # create-skill 规范目标：技能资产开放写（用户实测被拦截的修复点）
        assert not ws.is_protected(ws.resolve(".glaucous/skills/code-review/SKILL.md"))

    def test_outside_unchanged(self, ws: Workspace) -> None:
        assert not ws.is_protected(ws.resolve("src/a.py"))

    def test_is_skill_asset(self, ws: Workspace) -> None:
        assert ws.is_skill_asset(ws.resolve(".glaucous/skills/foo/SKILL.md"))
        assert not ws.is_skill_asset(ws.resolve(".glaucous/audit.log"))
        assert not ws.is_skill_asset(ws.resolve("src/x.md"))


class TestClassifierScope:
    def test_redirect_to_skills_not_dangerous(self, tmp_path: Path) -> None:
        clf = CommandClassifier(Workspace(tmp_path))
        # bash 重定向写 .glaucous/skills/ → 区内写（WRITE 走审批，不再 DANGEROUS 硬拦）
        assert clf.classify("echo hi > .glaucous/skills/notes.md")[0] == Risk.WRITE

    def test_redirect_to_audit_still_dangerous(self, tmp_path: Path) -> None:
        clf = CommandClassifier(Workspace(tmp_path))
        assert clf.classify("echo fake > .glaucous/audit.log")[0] == Risk.DANGEROUS


class TestReadScope:
    """v1.1 修订（用户决策 2026-08-30）：保护语义收窄为写完整性——
    区内读（含 .glaucous/ 运行日志）一律放行；区外读 WRITE 可同类型豁免。"""

    def test_read_tools_inside_protected_no_approval(self, ws: Workspace) -> None:
        from glaucous.tools.files import ListDirTool, ReadFileTool
        from glaucous.tools.search import GrepTool

        reader = ReadFileTool(ws)
        assert reader.build_approval({"path": ".glaucous/audit.log"}, "build") is None
        assert ListDirTool(ws).build_approval({"path": ".glaucous"}, "build") is None
        assert GrepTool(ws).build_approval({"path": ".glaucous"}, "build") is None

    def test_outside_read_write_risk(self, ws: Workspace, tmp_path: Path) -> None:
        from glaucous.tools.files import ReadFileTool

        action = ReadFileTool(ws).build_approval({"path": "../outside.py"}, "build")
        assert action is not None
        assert action.risk == Risk.WRITE  # 可同类型豁免（不再 DANGEROUS 守卫）

    def test_bash_read_protected_safe(self, tmp_path: Path) -> None:
        clf = CommandClassifier(Workspace(tmp_path))
        risk, _ = clf.classify("head -c 200 .glaucous/agents/20260830-180006-030c.jsonl")
        assert risk == Risk.SAFE  # 读运行日志与区内普通读同等

    def test_bash_write_protected_still_dangerous(self, tmp_path: Path) -> None:
        clf = CommandClassifier(Workspace(tmp_path))
        risk, _ = clf.classify("rm .glaucous/audit.log")
        assert risk == Risk.DANGEROUS  # 写完整性保护不变


class TestWriteToolSkillRefresh:
    @pytest.mark.asyncio
    async def test_write_skill_refreshes_index(self, ws: Workspace) -> None:
        calls: list[Path] = []
        tool = WriteFileTool(ws, on_skill_write=lambda: calls.append(ws.root))
        result = await tool.execute(
            path=".glaucous/skills/code-review/SKILL.md", content="---\nname: code-review\n---\n正文"
        )
        assert result.ok
        assert "技能索引已刷新" in result.content
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_write_regular_file_no_refresh(self, ws: Workspace) -> None:
        calls: list[Path] = []
        tool = WriteFileTool(ws, on_skill_write=lambda: calls.append(ws.root))
        result = await tool.execute(path="src/a.py", content="x = 1")
        assert result.ok
        assert "技能索引" not in result.content
        assert calls == []

    @pytest.mark.asyncio
    async def test_write_audit_log_still_blocked(self, ws: Workspace) -> None:
        tool = WriteFileTool(ws, on_skill_write=lambda: None)
        result = await tool.execute(path=".glaucous/audit.log", content="伪造审计")
        assert not result.ok
        assert "受保护目录" in result.content

    @pytest.mark.asyncio
    async def test_edit_skill_refreshes_index(self, ws: Workspace, tmp_path: Path) -> None:
        calls: list[Path] = []
        skill_file = ws.root / ".glaucous" / "skills" / "foo" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("旧正文", encoding="utf-8")
        tool = EditFileTool(ws, on_skill_write=lambda: calls.append(ws.root))
        result = await tool.execute(path=".glaucous/skills/foo/SKILL.md", old="旧正文", new="新正文")
        assert result.ok
        assert "技能索引已刷新" in result.content
        assert len(calls) == 1
