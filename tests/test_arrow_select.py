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
