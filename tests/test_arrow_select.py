"""R6 箭头选择器单测（v1.1 打磨 §6.1）：注入伪按键序列，不起真实终端。

覆盖：↑↓/j/k 循环移动、Enter 返回索引、Esc 返回 None、循环边界、异常回退。
"""

from __future__ import annotations

import pytest

from glaucous import cli

OPTIONS = ["同意", "同意同类型", "拒绝"]


def keys(*seq: str):
    """伪按键源：按序产出；耗尽抛 StopIteration（选择器异常回退路径兜底）。"""
    it = iter(seq)

    def read_key() -> str:
        return next(it)

    return read_key


class TestArrowSelect:
    def test_enter_first_option(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("enter")) == 0

    def test_down_then_enter(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("down", "enter")) == 1

    def test_up_wraps_to_last(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("up", "enter")) == 2

    def test_down_cycles_full_round(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("down", "down", "down", "enter")) == 0

    def test_jk_vim_style(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("j", "enter")) == 1
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("k", "enter")) == 2

    def test_esc_cancels(self) -> None:
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("esc")) is None

    def test_keyboard_interrupt_returns_none(self) -> None:
        """Ctrl+C → None，调用方走取消/数字回退（§6.1 异常契约）。"""

        def raise_ctrl_c() -> str:
            raise KeyboardInterrupt

        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=raise_ctrl_c) is None

    def test_generic_exception_returns_none(self) -> None:
        def broken() -> str:
            raise OSError("终端故障")

        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=broken) is None

    def test_unknown_keys_ignored(self) -> None:
        """无关按键（如误触字符）忽略，不改变选中项。"""
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("x", "z", "enter")) == 0


class TestRedrawProtocol:
    """重绘协议回归（用户 WSL 实测残影修复）：整块重绘 + 清屏，不产生残影叠加。"""

    def test_redraw_covers_full_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """原实现漏计提示行：重绘起点每次低一行，旧块被挤下去残影叠加
        （两个「请选择：」、选项重复显示）。修复后重绘必须覆盖整块。"""
        import io

        from rich.console import Console

        buf = io.StringIO()
        # 拆分重构：select_with_arrows 迁至 ui.interact，console 补丁须打在实现模块
        monkeypatch.setattr("glaucous.ui.interact.console", Console(file=buf, width=80))
        assert cli.select_with_arrows("请选择：", OPTIONS, read_key=keys("down", "enter")) == 1
        out = buf.getvalue()
        assert out.count("请选择：") == 2      # 初绘 + 一次重绘，不叠加
        assert out.count("↑↓ 选择") == 2       # 提示行纳入重绘块（原实现在块外只绘一次）
        assert "\x1b[5A\x1b[J" in out          # 回块首 5 行（3 选项 → 块高 5）+ 清屏到底

    def test_cjk_option_single_line_no_wrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CJK 显示宽度按 2 格计量：超宽中文选项截断为单行，不折行引起漂移。"""
        import io

        from rich.console import Console

        buf = io.StringIO()
        monkeypatch.setattr("glaucous.ui.interact.console", Console(file=buf, width=20))
        long_option = "同" * 30
        assert cli.select_with_arrows("请选择：", [long_option], read_key=keys("enter")) == 0
        out = buf.getvalue()
        assert "…" in out                       # 超宽被截断
        # 单选项块高 3：截断后每行不超终端宽（无折行则行内容各占一行）
        assert out.count("请选择：") == 1
