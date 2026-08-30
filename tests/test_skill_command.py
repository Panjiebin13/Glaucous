"""/skill 手动调用与 /skills 文案单测（v1.1 反馈修复批次 F2/F3）。

覆盖 spec §二/§三：_cmd_skill 解析（无参提示、未知名报错、省略描述、附描述、
完全相等匹配）、pending_task 组装模板、skill_text 纯读取无加载态、
/skills 无加载状态展示 + 说明行。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from glaucous.commands import _cmd_skill, _cmd_skills
from glaucous.extensions.skills import SkillRegistry

SKILL_BODY = "1. 通读变更文件\n2. 按清单逐项检查"


@pytest.fixture()
def registry(tmp_path: Path) -> SkillRegistry:
    """项目级技能注册表：一个 code-review 技能（含 frontmatter 与正文）。"""
    skill_dir = tmp_path / ".glaucous" / "skills" / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: 代码评审\n---\n" + SKILL_BODY,
        encoding="utf-8",
    )
    reg = SkillRegistry(tmp_path)
    reg.scan()
    return reg


def make_ctx(registry: SkillRegistry) -> SimpleNamespace:
    return SimpleNamespace(skills=registry, renderer=FakeRenderer(), pending_task=None)


class FakeRenderer:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.errors: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(str(text))

    def info(self, text: str) -> None:
        self.notes.append(str(text))

    def error(self, text: str) -> None:
        self.errors.append(str(text))


class TestSkillCommand:
    @pytest.mark.asyncio
    async def test_no_arg_hints_usage_and_names(self, registry: SkillRegistry) -> None:
        ctx = make_ctx(registry)
        assert await _cmd_skill(ctx, "") is True
        assert any("用法：/skill <名> [任务描述]" in n for n in ctx.renderer.notes)
        assert any("code-review" in n for n in ctx.renderer.notes)
        assert ctx.pending_task is None  # 未组装任务

    @pytest.mark.asyncio
    async def test_unknown_name_errors(self, registry: SkillRegistry) -> None:
        ctx = make_ctx(registry)
        assert await _cmd_skill(ctx, "no-such") is True
        assert any("未注册技能" in e for e in ctx.renderer.errors)
        assert any("code-review" in e for e in ctx.renderer.errors)  # 报错附可用列表
        assert ctx.pending_task is None

    @pytest.mark.asyncio
    async def test_exact_match_only(self, registry: SkillRegistry) -> None:
        """名字完全相等匹配（S6）：前缀/模糊不命中。"""
        ctx = make_ctx(registry)
        await _cmd_skill(ctx, "code")  # "code-review" 的前缀
        assert ctx.pending_task is None
        assert ctx.renderer.errors

    @pytest.mark.asyncio
    async def test_assembles_without_description(self, registry: SkillRegistry) -> None:
        ctx = make_ctx(registry)
        await _cmd_skill(ctx, "code-review")
        assert ctx.pending_task is not None
        assert "[技能 code-review]" in ctx.pending_task
        assert SKILL_BODY in ctx.pending_task
        assert ctx.pending_task.endswith("用户任务：按技能指令执行")  # 描述省略的兜底指令

    @pytest.mark.asyncio
    async def test_assembles_with_description(self, registry: SkillRegistry) -> None:
        ctx = make_ctx(registry)
        await _cmd_skill(ctx, "code-review 检查最近一次提交")
        assert ctx.pending_task is not None
        assert "用户任务：检查最近一次提交" in ctx.pending_task


class TestSkillTextNoSideEffect:
    def test_skill_text_returns_body(self, registry: SkillRegistry) -> None:
        assert registry.skill_text("code-review") == SKILL_BODY

    def test_skill_text_unknown_returns_none(self, registry: SkillRegistry) -> None:
        assert registry.skill_text("no-such") is None

    def test_skill_text_marks_nothing_loaded(self, registry: SkillRegistry) -> None:
        """F2/F3 决策：手动调用不产生加载态（加载状态不对外呈现）。"""
        registry.skill_text("code-review")
        assert registry.loaded_names() == set()


class TestPendingTaskConsumption:
    """repl 消费链路（spec §五）：消费后置 None、仅驱动一次、当次生效不污染 system prompt。"""

    def test_consume_returns_task_and_resets(self, registry: SkillRegistry) -> None:
        from glaucous.cli import consume_pending_task

        ctx = make_ctx(registry)
        ctx.pending_task = "组装好的任务"
        assert consume_pending_task(ctx) == "组装好的任务"
        assert ctx.pending_task is None  # 消费后置 None：仅驱动一次
        assert consume_pending_task(ctx) is None  # 二次消费无任务（不重复驱动）

    @pytest.mark.asyncio
    async def test_skill_command_does_not_touch_system_prompt(self, registry: SkillRegistry) -> None:
        """当次生效（spec §3.2）：_cmd_skill 只写 pending_task，不注入 system prompt。"""
        ctx = make_ctx(registry)
        ctx.system_prompt = "原始系统提示词"
        await _cmd_skill(ctx, "code-review")
        assert ctx.system_prompt == "原始系统提示词"
        assert SKILL_BODY not in ctx.system_prompt
        assert SKILL_BODY in ctx.pending_task  # 技能正文仅在当轮任务文本中


class TestSkillsListingNoLoadState:
    @pytest.mark.asyncio
    async def test_no_load_state_words(self, registry: SkillRegistry) -> None:
        """/skills 不出现任何加载状态字样（用户反馈②：避免误导）。"""
        ctx = make_ctx(registry)
        assert await _cmd_skills(ctx) is True
        joined = "\n".join(ctx.renderer.notes + ctx.renderer.errors)
        assert "未加载" not in joined and "已加载" not in joined
        assert "code-review" in joined and "代码评审" in joined

    @pytest.mark.asyncio
    async def test_usage_hint_line(self, registry: SkillRegistry) -> None:
        """底部说明行（spec §二）。"""
        ctx = make_ctx(registry)
        await _cmd_skills(ctx)
        assert any(
            "技能在任务匹配时自动生效；也可用 /skill <名> [任务描述] 手动调用。" in n
            for n in ctx.renderer.notes
        )
