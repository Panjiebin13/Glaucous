"""R2 REPL 补全器单测（v1.1 打磨 §2）：命令段前缀补全 / 路径段补全 / 其他段无候选。

补全器为 prompt_toolkit Completer：用 Document + CompleteEvent 驱动，
不启动真实输入会话（管道/降级不受影响）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glaucous import cli
from glaucous.commands import COMMAND_META


def complete(completer, text: str) -> list:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    doc = Document(text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# guide", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    return tmp_path


class TestCommandCompletion:
    def test_slash_lists_all_commands(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace)
        texts = {c.text for c in complete(completer, "/")}
        # 键入 / 立即列出全部命令（含 /view、/expand 与别名 /quit）
        assert {"/view", "/expand", "/quit", "/help"} <= texts
        assert len(texts) == len(COMMAND_META) + 1  # COMMAND_META + /quit 别名

    def test_prefix_filters(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace)
        texts = {c.text for c in complete(completer, "/vi")}
        assert texts == {"/view"}

    def test_meta_from_command_meta(self, workspace: Path) -> None:
        """候选 meta 取自 commands.COMMAND_META（单一数据源，S10）。"""
        completer = cli.make_repl_completer(workspace)
        (item,) = complete(completer, "/exp")
        assert item.text == "/expand"
        assert item.display_meta_text == COMMAND_META["/expand"]


class TestPathCompletion:
    def test_candidates_after_space(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace)
        texts = {c.text for c in complete(completer, "/view ")}
        assert "docs/" in texts          # 目录尾缀 / 便于继续深入
        assert "README.md" in texts
        assert "main.py" in texts
        assert not any(t.startswith((".git", "__pycache__")) for t in texts)  # 排除目录

    def test_prefix_filters_path(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace)
        texts = {c.text for c in complete(completer, "/view do")}
        assert texts == {"docs/"}

    def test_nested_path_completion(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace)
        texts = {c.text for c in complete(completer, "/view docs/")}
        assert texts == {"docs/guide.md"}

    def test_model_without_source_no_candidates(self, workspace: Path) -> None:
        """未提供 model_names 数据源时 /model 无候选（降级安全，不抛错）。"""
        completer = cli.make_repl_completer(workspace)
        assert complete(completer, "/model ") == []

    def test_traversal_error_silent(self, tmp_path: Path) -> None:
        """遍历异常（目录不存在）静默返回空候选，不抛错（§2.2）。"""
        completer = cli.make_repl_completer(tmp_path / "not-exists")
        assert complete(completer, "/view ") == []


class TestModelCompletion:
    """F1 /model 参数补全（spec §1.3）：候选经 model_names() 动态取值。"""

    def test_lists_all_when_no_arg(self, workspace: Path) -> None:
        names = ["deepseek-chat", "deepseek-reasoner", "gpt-4o"]
        completer = cli.make_repl_completer(workspace, model_names=lambda: names)
        texts = {c.text for c in complete(completer, "/model ")}
        assert texts == {"deepseek-chat", "deepseek-reasoner", "gpt-4o"}

    def test_prefix_filters(self, workspace: Path) -> None:
        names = ["deepseek-chat", "deepseek-reasoner", "gpt-4o"]
        completer = cli.make_repl_completer(workspace, model_names=lambda: names)
        texts = {c.text for c in complete(completer, "/model deep")}
        assert texts == {"deepseek-chat", "deepseek-reasoner"}

    def test_dynamic_callable_follows_registry(self, workspace: Path) -> None:
        """延迟取值：列表变化后候选跟随（不缓存快照，§1.3）。"""
        names = ["model-a"]
        completer = cli.make_repl_completer(workspace, model_names=lambda: list(names))
        assert {c.text for c in complete(completer, "/model ")} == {"model-a"}
        names.append("model-b")
        assert {c.text for c in complete(completer, "/model ")} == {"model-a", "model-b"}

    def test_no_match_no_candidates(self, workspace: Path) -> None:
        completer = cli.make_repl_completer(workspace, model_names=lambda: ["deepseek-chat"])
        assert complete(completer, "/model gpt") == []


class TestFreeText:
    def test_free_text_no_completion(self, workspace: Path) -> None:
        """自由对话段不弹补全（需求 2 边界）。"""
        completer = cli.make_repl_completer(workspace)
        assert complete(completer, "帮我写一个排序函数") == []
