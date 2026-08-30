"""R7 最终回答 Markdown 卡片单测（v1.1 打磨 §7，S9 归属）。

覆盖：卡片含标题与正文（markdown.* 色板渲染）、空文本/纯空白不渲染；
管道/非 TTY 下 cli 不触发卡片（触发条件 = session 非 None，make_prompt_session
三条件已含 stdout TTY 判定，此处以渲染函数单测 + 条件断言覆盖）。
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from glaucous import theme


@pytest.fixture()
def capture_console(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """把 theme.console 换成 StringIO 捕获（无终端），返回缓冲区。"""
    buf = io.StringIO()
    monkeypatch.setattr(theme, "console", Console(file=buf, force_terminal=False, theme=theme.THEME, highlight=False))
    return buf


class TestRenderAnswerCard:
    def test_card_contains_title_and_body(self, capture_console: io.StringIO) -> None:
        theme.render_answer_card("# 标题\n\n- 列表项 **加粗**")
        out = capture_console.getvalue()
        assert "回答" in out          # 卡片标题 🕊 回答
        assert "标题" in out          # Markdown 正文经 rich 渲染
        assert "列表项" in out

    def test_empty_text_not_rendered(self, capture_console: io.StringIO) -> None:
        theme.render_answer_card("")
        theme.render_answer_card("   \n  ")
        assert capture_console.getvalue() == ""

    def test_none_answer_not_rendered(self, capture_console: io.StringIO) -> None:
        theme.render_answer_card(None)  # type: ignore[arg-type]
        assert capture_console.getvalue() == ""


class TestTriggerCondition:
    def test_degraded_mode_no_prompt_session(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """降级路径（GLAUCOUS_INPUT=plain；管道/非 TTY 同属三条件）→ session 为 None，
        repl 的卡片触发条件（session is not None）不成立（§7 触发条件）。"""
        monkeypatch.setenv("GLAUCOUS_INPUT", "plain")
        from glaucous.cli import make_prompt_session

        assert make_prompt_session(tmp_path) is None
