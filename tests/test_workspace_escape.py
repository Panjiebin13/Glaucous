"""沙箱逃逸矩阵单测（任务 1.7 / 债务项「沙箱逃逸矩阵」，概设 test_workspace_escape.py）。

覆盖：`../` 穿越、绝对路径区外、符号链接逃逸、只读白名单放行、
区外读触发审批（classify_path WRITE）、受保护目录 `.glaucous/`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glaucous.permission.risk import Risk
from glaucous.permission.workspace import Workspace, WorkspaceEscape


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root)


class TestResolve:
    def test_relative_joined_to_root(self, ws: Workspace) -> None:
        assert ws.resolve("src/a.py") == ws.root / "src" / "a.py"

    def test_dotdot_normalized(self, ws: Workspace) -> None:
        # `src/../a.py` 规范化为根下 a.py——规范化本身不构成逃逸
        assert ws.resolve("src/../a.py") == ws.root / "a.py"

    def test_absolute_kept(self, ws: Path, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere" / "x.txt"
        assert ws.resolve(str(outside)) == outside.resolve()


class TestWithin:
    def test_inside_true(self, ws: Workspace) -> None:
        assert ws.is_within(ws.resolve("a.txt"))

    def test_dotdot_escape_false(self, ws: Workspace) -> None:
        assert not ws.is_within(ws.resolve("../outside.txt"))

    def test_absolute_outside_false(self, ws: Workspace, tmp_path: Path) -> None:
        assert not ws.is_within(ws.resolve(str(tmp_path / "other" / "f.txt")))


class TestCheck:
    def test_dotdot_escape_raises(self, ws: Workspace) -> None:
        with pytest.raises(WorkspaceEscape):
            ws.check("../evil.txt")

    def test_inside_returns_normalized(self, ws: Workspace) -> None:
        assert ws.check("src/../a.txt") == ws.root / "a.txt"


class TestClassifyPath:
    def test_inside_safe(self, ws: Workspace) -> None:
        assert ws.classify_path("a.txt") == Risk.SAFE

    def test_outside_read_is_write(self, ws: Workspace, tmp_path: Path) -> None:
        # FR-13：读区外仍需审批——标 WRITE 走审批管线，而非直接拒绝
        assert ws.classify_path(str(tmp_path / "outside" / "cfg.yml")) == Risk.WRITE

    def test_read_only_extra_safe(self, tmp_path: Path) -> None:
        extra = tmp_path / "sdk"
        extra.mkdir()
        ws = Workspace(tmp_path / "ws" if (tmp_path / "ws").exists() else tmp_path, read_only_extra=[extra])
        assert ws.classify_path(str(extra / "env.py")) == Risk.SAFE

    def test_protected_dir(self, ws: Workspace) -> None:
        assert ws.is_protected(ws.resolve(".glaucous/audit.log"))
        assert not ws.is_protected(ws.resolve("src/a.py"))


class TestSymlinkEscape:
    def test_symlink_pointing_outside(self, ws: Workspace, tmp_path: Path) -> None:
        outside = tmp_path / "vault"
        outside.mkdir()
        link = ws.root / "leak"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink 创建需要权限（Windows 非管理员环境）")
        resolved = ws.resolve("leak/secret.txt")
        assert not ws.is_within(resolved)
        assert ws.classify_path("leak/secret.txt") == Risk.WRITE
        with pytest.raises(WorkspaceEscape):
            ws.check("leak/secret.txt")
