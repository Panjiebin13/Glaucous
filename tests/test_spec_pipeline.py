"""Spec 编排单测（v1.1-M5 任务 5.7，spec §七 6~15）。

PipelineHooks 全 fake（脚本化 run_turn/run_review/ask/checkpoint）：
全流程回放、评审修订回环、四类轮次耗尽升级、深度介入、任务失败、
截断回读、契约解析失败、非 Git 降级、cancel/status、ask None 语义。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from glaucous.spec.pipeline import MAX_ROUNDS, PipelineHooks, SpecPipeline
from glaucous.spec.store import SpecStore

FULL_BODY = """\
## 需求与边界
目标：修 add。

## 澄清记录
- 问：范围？答：仅 calc。

## 约束
- 不改目录结构

## 设计
改一行。

## 任务清单
- [ ] 修正 add 逻辑
- [ ] 补测试

## 验收标准
- add(1,2)==3（验证方式：单测）
- 测试全绿（验证方式：pytest）

## 风险与回退
- 无
"""

PASS = "评审结论：通过\n【阻塞级问题】无\n【建议级问题】无"
FAIL = "评审结论：不通过\n【阻塞级问题】1. 验收标准缺验证方式\n【建议级问题】无"
# 验收行带列表标记（实测形态，用户验收 2026-08-31：解析需先剥「- 」）
ACC_ALL_OK = ("- ✓ add(1,2)==3（验证方式：单测）\n- ✓ 测试全绿（验证方式：pytest）\n核验结论：全部达成")


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


class FakeHooks:
    """脚本化 hooks：ask 按问题前缀查表；run_turn/run_review 按队列消费。"""

    def __init__(self, ask_map: dict[str, str | None] | None = None):
        self.turns: list[str] = []          # run_turn 应答队列
        self.reviews: list[tuple[str, dict]] = []  # (报告, metadata) 队列
        self.ask_map = ask_map or {}
        self.questions: list[str] = []
        self.checkpoint_labels: list[str] = []
        self.review_contexts: list[str] = []
        self._seq = 0

    async def run_turn(self, message: str) -> str:
        return self.turns.pop(0)

    async def run_review(self, task: str, context: str):
        self.review_contexts.append(context)
        report, meta = self.reviews.pop(0)
        return report, meta

    async def ask(self, question: str, options: list[str]):
        self.questions.append(question)
        for key, answer in self.ask_map.items():
            if key in question:
                return answer
        return options[0] if options else None  # 缺省取首项（便于脚本省略）

    def checkpoint(self, label: str):
        self.checkpoint_labels.append(label)
        self._seq += 1
        return self._seq

    def build(self) -> PipelineHooks:
        return PipelineHooks(
            run_turn=self.run_turn, run_review=self.run_review,
            ask=self.ask, checkpoint=self.checkpoint,
        )


def make_ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=tmp_path, renderer=FakeRenderer(), checkpoint_store=None
    )


def full_flow_hooks() -> FakeHooks:
    hooks = FakeHooks({
        "需求是否已澄清": "进入起草",
        "评审循环模式": "全自动",
        "是否批准执行": "批准执行",
    })
    hooks.turns = ["澄清完成：修复 calc 的 add 函数", FULL_BODY, "任务1完成", "任务2完成"]
    hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
    return hooks


class TestFullFlow:
    def test_happy_path_to_verified(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        hooks = full_flow_hooks()
        asyncio.run(SpecPipeline(ctx, hooks.build()).start("修复 add"))
        docs = SpecStore(tmp_path).list_all()
        assert len(docs) == 1
        doc = docs[0]
        assert doc.status == "verified"
        assert doc.meta["approved_at"]
        assert doc.meta["entry_checkpoint"] == 1
        assert [done for _no, done, _t in doc.tasks()] == [True, True]
        # checkpoint hook 恰 3 次：入口基线 1 + 每任务 1（r1-B1 定案）
        assert len(hooks.checkpoint_labels) == 3
        assert "执行入口" in hooks.checkpoint_labels[0]
        assert "任务 1" in hooks.checkpoint_labels[1]
        assert "任务 2" in hooks.checkpoint_labels[2]
        assert any("验收通过" in n for n in ctx.renderer.notes)
        # R2：全自动 + 评审正常通过 → 跳过批准卡直接执行（无批准询问，有跳过提示）
        assert not any("是否批准执行" in q for q in hooks.questions)
        assert any("跳过批准卡" in n for n in ctx.renderer.notes)

    def test_cancel_command(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        doc = store.create("待取消", FULL_BODY)
        hooks = FakeHooks({"确认终止": "确认归档"})
        pipeline = SpecPipeline(make_ctx(tmp_path), hooks.build())
        asyncio.run(pipeline.cancel(doc))
        assert store.load(doc.spec_id).status == "archived"
        assert "spec cancel" in store.load(doc.spec_id).body

    def test_status_lines(self, tmp_path: Path) -> None:
        from glaucous.commands import _spec_status_lines

        store = SpecStore(tmp_path)
        doc = store.create("看进度", FULL_BODY)
        store.check_task(doc, 1)
        lines = _spec_status_lines(doc)
        assert any("1/2 已完成" in line for line in lines)
        assert any("[x] 修正 add 逻辑" in line for line in lines)


    def test_deep_mode_keeps_approval_card(self, tmp_path: Path) -> None:
        # R2 反面：深度介入模式即使评审正常通过仍呈批准卡（用户介入点保留）
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "深度介入（每轮可提建议）",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2"]
        hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert any("是否批准执行" in q for q in hooks.questions)
        assert SpecStore(tmp_path).list_all()[0].status == "verified"


class TestReviewLoop:
    def test_revise_then_pass(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        hooks = FakeHooks({"需求是否已澄清": "进入起草", "评审循环模式": "全自动",
                           "是否批准执行": "批准执行"})
        hooks.turns = ["澄清完成：x", FULL_BODY, FULL_BODY, FULL_BODY, "t1", "t2"]
        hooks.reviews = [(FAIL, {"ok": True})] * 2 + [(PASS, {"ok": True}),
                         (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        asyncio.run(SpecPipeline(ctx, hooks.build()).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "verified"
        assert "## 任务清单" in doc.body  # 修订已写回（round 为共用字段，随 code_review 语义重计，§2.1）
        # Spec 评审共 3 轮（两轮不通过后第三轮通过；以 Spec 专属清单标题计数，
        # 代码评审/验收上下文不含该标题）
        assert len([c for c in hooks.review_contexts if "评审检查清单（Spec 评审）" in c]) == 3

    def test_exhaustion_escalate_to_approval(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "评审已 3 轮未通过": "呈请批准（带未决阻塞）",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY] + [FULL_BODY] * 2 + ["t1", "t2"]
        hooks.reviews = [(FAIL, {"ok": True})] * 3 + [(PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        asyncio.run(SpecPipeline(ctx, hooks.build()).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "verified"
        assert any("评审已 3 轮未通过" in q for q in hooks.questions)

    def test_exhaustion_cancel(self, tmp_path: Path) -> None:
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "评审已 3 轮未通过": "取消归档",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY] + [FULL_BODY] * 2
        hooks.reviews = [(FAIL, {"ok": True})] * 3
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "archived"

    def test_verdict_parse_failure_conservative(self, tmp_path: Path) -> None:
        # 契约解析失败 → 保守判不通过（决策 5）→ 3 轮耗尽 → 取消归档
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "评审已 3 轮未通过": "取消归档",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY] + [FULL_BODY] * 2
        hooks.reviews = [("完全无法解析的自由文本", {"ok": True})] * 3
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "archived"

    def test_deep_intervention_feedback_injected(self, tmp_path: Path) -> None:
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "深度介入（每轮可提建议）",
            "请对本轮修订提一句建议": "把验收标准写具体",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, FULL_BODY, "t1", "t2"]
        hooks.reviews = [(FAIL, {"ok": True}), (PASS, {"ok": True}),
                         (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert any("把验收标准写具体" in c for c in hooks.review_contexts)


class TestExhaustionOtherGates:
    def test_clarify_exhaustion(self, tmp_path: Path) -> None:
        # 澄清 3 轮耗尽 → [仍要起草/取消]
        hooks = FakeHooks({"需求是否已澄清": "继续澄清", "澄清已达 3 轮": "取消"})
        hooks.turns = ["答1", "答2", "答3"]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all() == []
        assert any("澄清已达 3 轮" in q for q in hooks.questions)

    def test_approval_exhaustion(self, tmp_path: Path) -> None:
        # 批准反馈修订 3 轮耗尽 → [仍要批准/归档] → 归档（深度介入模式保证批准卡呈现，R2 后全自动会跳过）
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "深度介入（每轮可提建议）",
            "是否批准执行": "提修改意见", "请输入修改意见": "改改",
            "批准修订已 3 轮": "归档",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY] + [FULL_BODY] * 3
        hooks.reviews = [(PASS, {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "archived"

    def test_code_review_exhaustion(self, tmp_path: Path) -> None:
        # 代码评审 3 轮耗尽 → [按现状出验收报告/…] → 验收有 ✗ → archived
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
            "代码评审已 3 轮未通过": "按现状出验收报告",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2", "修1", "修2"]
        hooks.reviews = [(PASS, {"ok": True})] + [(FAIL, {"ok": True})] * 3 + [
            ("✗ add(1,2)==3：未达成\n核验结论：存在未决", {"ok": True})
        ]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "archived"
        assert "验收存在未决" in doc.body


class TestExecution:
    def test_task_failure_retry_then_skip(self, tmp_path: Path) -> None:
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t2 完成"]
        hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        hooks2 = hooks.build()

        calls = {"n": 0}
        turn_queue = list(hooks.turns)  # 非任务消息仍走队列（澄清/起草/任务2）
        hooks.turns = []

        async def flaky(message: str) -> str:
            if "[Spec 执行·任务 1]" in message:
                calls["n"] += 1
                raise RuntimeError(f"故障{calls['n']}")
            return turn_queue.pop(0)

        failure_answers = iter(["重试", "跳过该任务"])
        orig_ask = hooks2.ask

        async def ask2(question: str, options: list[str]):
            if "执行失败" in question and options and options[0] == "重试":
                return next(failure_answers)
            return await orig_ask(question, options)

        hooks2.run_turn = flaky  # type: ignore[assignment]
        hooks2.ask = ask2  # type: ignore[assignment]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks2).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "verified"
        assert "任务 1 跳过" in doc.body  # 跳过项登记风险节
        assert [done for _no, done, _t in doc.tasks()] == [False, True]
        assert calls["n"] == 2  # 重试了一次（不重复打快照：checkpoint 仍 3 次）
        assert len(hooks.checkpoint_labels) == 3

    def test_task_failure_abort(self, tmp_path: Path) -> None:
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行", "如何处理？": "归档中止",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY]
        hooks.reviews = [(PASS, {"ok": True})]
        hooks2 = hooks.build()
        turn_queue = list(hooks.turns)  # 非任务消息仍走队列（澄清/起草）
        hooks.turns = []

        async def boom(message: str) -> str:
            if "[Spec 执行·任务 1]" in message:
                raise RuntimeError("致命故障")
            return turn_queue.pop(0)

        orig_ask = hooks2.ask

        async def ask_abort(question: str, options: list[str]):
            if "执行失败" in question:
                return "归档中止"
            return await orig_ask(question, options)

        hooks2.run_turn = boom  # type: ignore[assignment]
        hooks2.ask = ask_abort  # type: ignore[assignment]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks2).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "archived"
        assert "用户中止" in doc.body


class TestDegradationAndRecovery:
    def test_report_truncation_reread(self, tmp_path: Path) -> None:
        # 决策 6：截断报告经 outputs/ 归档回读
        from glaucous.tools.base import ToolResult

        outputs = tmp_path / "outputs"
        outputs.mkdir()
        (outputs / "spawn_agent-child-9.log").write_text("完整评审报告正文", encoding="utf-8")
        ctx = SimpleNamespace(
            workspace=tmp_path, renderer=FakeRenderer(), outputs_dir=outputs,
            subagent_runner=SimpleNamespace(run=None), checkpoint_store=None,
        )

        async def fake_run(task: str, context: str) -> ToolResult:
            return ToolResult(
                ok=True, content="截断…（报告已截断）",
                metadata={"sub_agent": "child-9"},
            )

        ctx.subagent_runner.run = fake_run  # type: ignore[attr-defined]
        pipeline = SpecPipeline(ctx, hooks=None)
        report, metadata = asyncio.run(pipeline._hooks.run_review("t", "c"))
        assert report == "完整评审报告正文"
        assert metadata["ok"] is True

    def test_non_git_checkpoint_none(self, tmp_path: Path) -> None:
        # 决策 8/13：checkpoint hook 返回 None → 执行继续、评审输入含降级说明
        ctx = make_ctx(tmp_path)
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2"]
        hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        hooks2 = hooks.build()
        hooks2.checkpoint = lambda label: None  # type: ignore[method-assign]
        asyncio.run(SpecPipeline(ctx, hooks2).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "verified"
        assert doc.meta["entry_checkpoint"] is None
        assert any("无 diff" in c for c in hooks.review_contexts)
        assert any("checkpoint 不可用" in n for n in ctx.renderer.notes)

    def test_ask_none_at_clarify_cancels(self, tmp_path: Path) -> None:
        # 决策 13：澄清门 None → 取消（不静默推进）
        hooks = FakeHooks({"需求是否已澄清": None})
        hooks.turns = ["澄清完成：x"]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all() == []

    def test_ask_none_at_approval_archives(self, tmp_path: Path) -> None:
        # 决策 13：批准卡 None → 视为空意见 → 归档（深度介入保证批准卡呈现，R2）
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "深度介入（每轮可提建议）",
            "是否批准执行": None, "请输入修改意见": None,
        })
        hooks.turns = ["澄清完成：x", FULL_BODY]
        hooks.reviews = [(PASS, {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "archived"


class TestRoundCap:
    def test_max_rounds_is_three(self) -> None:
        assert MAX_ROUNDS == 3  # 决策 9：硬编码口径显式断言


class TestGoalParsing:
    def test_goal_with_inner_colon_not_split(self) -> None:
        # S6：目标行内含半角冒号不被二次截断
        assert SpecPipeline._goal_of("澄清完成：修复模块: core 的 bug", "需求") == "修复模块: core 的 bug"

    def test_goal_without_colon(self) -> None:
        assert SpecPipeline._goal_of("澄清完成 直接给目标", "需求原文") == "直接给目标"

    def test_goal_fallback_to_requirement(self) -> None:
        assert SpecPipeline._goal_of("没有标记的自由回答", "需求原文") == "需求原文"


class TestEscalationExtraRound:
    def test_spec_review_extra_revision_round(self, tmp_path: Path) -> None:
        # §7-8①：耗尽升级选「再修订一轮（仅一次）」→ 修订 + 加轮评审 → 批准链
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "评审已 3 轮未通过": "再修订一轮（仅一次）",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY] + [FULL_BODY] * 3 + ["t1", "t2"]
        hooks.reviews = [(FAIL, {"ok": True})] * 4 + [(PASS, {"ok": True}), (ACC_ALL_OK, {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "verified"

    def test_code_review_extra_fix_round(self, tmp_path: Path) -> None:
        # 代码评审耗尽升级选「再修复一轮（仅一次）」→ 修复后复审通过 → 验收全 ✓
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
            "代码评审已 3 轮未通过": "再修复一轮（仅一次）",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2", "修1", "修2", "修3"]
        hooks.reviews = [(PASS, {"ok": True})] + [(FAIL, {"ok": True})] * 3 + [
            (PASS, {"ok": True}),  # 加轮复审（新增，§4.5「复审」）
            (ACC_ALL_OK, {"ok": True}),
        ]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "verified"


class TestAcceptanceConservative:
    def test_contract_violation_not_verified(self, tmp_path: Path) -> None:
        # B3 作者裁决：验收报告无 ✓ 行（契约违约）不判 verified → archived
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2"]
        hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}),
                         ("自由文本，无任何核验行", {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        doc = SpecStore(tmp_path).list_all()[0]
        assert doc.status == "archived"
        assert doc.meta.get("acceptance") == "存在未决"
        assert "契约违约" in doc.body

    def test_partial_check_not_verified(self, tmp_path: Path) -> None:
        # ✓ 行数 < 标准数（只核验部分）不放行（B3 口径）
        hooks = FakeHooks({
            "需求是否已澄清": "进入起草", "评审循环模式": "全自动",
            "是否批准执行": "批准执行",
        })
        hooks.turns = ["澄清完成：x", FULL_BODY, "t1", "t2"]
        hooks.reviews = [(PASS, {"ok": True}), (PASS, {"ok": True}),
                         ("✓ add(1,2)==3（验证方式：单测）\n核验结论：全部达成", {"ok": True})]
        asyncio.run(SpecPipeline(make_ctx(tmp_path), hooks.build()).start("x"))
        assert SpecStore(tmp_path).list_all()[0].status == "archived"


class TestCommandRouting:
    """命令层接线（B1）：handle_command/_cmd_spec/_cmd_specs 路由与降级提示，
    pipeline 以替身注入（monkeypatch SpecPipeline）。"""

    @staticmethod
    def _ctx(tmp_path: Path) -> SimpleNamespace:
        return SimpleNamespace(workspace=tmp_path, renderer=FakeRenderer())

    @staticmethod
    def _patch_pipeline(monkeypatch, calls: dict) -> None:
        import glaucous.spec.pipeline as sp

        class FakePipeline:
            def __init__(self, ctx, hooks=None):
                calls["init"] = calls.get("init", 0) + 1

            async def start(self, requirement: str) -> None:
                calls["start"] = requirement

            async def resume(self, doc) -> None:
                calls["resume"] = doc.spec_id

            async def cancel(self, doc) -> None:
                calls["cancel"] = doc.spec_id

            async def ask_continue(self, doc) -> None:
                calls["ask_continue"] = doc.spec_id

        monkeypatch.setattr(sp, "SpecPipeline", FakePipeline)

    def test_spec_start_routes(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        asyncio.run(_cmd_spec(self._ctx(tmp_path), "给我做个大功能"))
        assert calls["start"] == "给我做个大功能"

    def test_specs_empty_hint(self, tmp_path: Path) -> None:
        from glaucous.commands import _cmd_specs

        ctx = self._ctx(tmp_path)
        asyncio.run(_cmd_specs(ctx))
        assert any("尚无 Spec" in n for n in ctx.renderer.notes)

    def test_spec_no_active_usage(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        ctx = self._ctx(tmp_path)
        asyncio.run(_cmd_spec(ctx, ""))
        assert any("用法" in n for n in ctx.renderer.notes)
        assert "start" not in calls

    def test_spec_cancel_no_active(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        ctx = self._ctx(tmp_path)
        asyncio.run(_cmd_spec(ctx, "cancel"))
        assert any("无活跃 Spec" in n for n in ctx.renderer.notes)
        assert "cancel" not in calls

    def test_spec_cancel_routes_to_active(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        store = SpecStore(tmp_path)
        doc = store.create("待取消", FULL_BODY)
        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        asyncio.run(_cmd_spec(self._ctx(tmp_path), "cancel"))
        assert calls["cancel"] == doc.spec_id

    def test_spec_status_shows_card(self, tmp_path: Path) -> None:
        from glaucous.commands import _cmd_spec

        store = SpecStore(tmp_path)
        doc = store.create("看状态", FULL_BODY)
        ctx = self._ctx(tmp_path)
        asyncio.run(_cmd_spec(ctx, "status"))
        assert any(doc.spec_id in n for n in ctx.renderer.notes)

    def test_spec_noarg_executing_asks_continue(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        store = SpecStore(tmp_path)
        doc = store.create("跑到一半", FULL_BODY)
        store.transition(doc, "reviewing")
        store.transition(doc, "approved")
        store.transition(doc, "executing")
        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        asyncio.run(_cmd_spec(self._ctx(tmp_path), ""))
        assert calls["ask_continue"] == doc.spec_id

    def test_spec_noarg_non_executing_hint(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import _cmd_spec

        store = SpecStore(tmp_path)
        store.create("评审中", FULL_BODY)
        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        ctx = self._ctx(tmp_path)
        asyncio.run(_cmd_spec(ctx, ""))
        assert any("不支持自动续跑" in n for n in ctx.renderer.notes)
        assert "ask_continue" not in calls

    def test_handle_command_dispatch(self, tmp_path: Path, monkeypatch) -> None:
        from glaucous.commands import handle_command

        calls: dict = {}
        self._patch_pipeline(monkeypatch, calls)
        ctx = SimpleNamespace(workspace=tmp_path, renderer=FakeRenderer())
        asyncio.run(handle_command("/spec 修个东西", ctx))
        assert calls["start"] == "修个东西"
        asyncio.run(handle_command("/specs", ctx))
        assert any("尚无 Spec" in n for n in ctx.renderer.notes)  # S1：补断言


class TestHardening:
    def test_cancel_exception_not_fatal(self, tmp_path: Path) -> None:
        # r2-S2：cancel 异常兜底回归固化（终态文档不可迁移 → 报错不击穿）
        store = SpecStore(tmp_path)
        doc = store.create("已归档", FULL_BODY)
        store.transition(doc, "archived")
        hooks = FakeHooks({"确认终止": "确认归档"})
        ctx = make_ctx(tmp_path)
        asyncio.run(SpecPipeline(ctx, hooks.build()).cancel(doc))
        assert any("取消失败" in e for e in ctx.renderer.errors)
        assert store.load(doc.spec_id).status == "archived"

    def test_strip_fences(self) -> None:
        # r2-S4：围栏剥离纯函数用例（§九.6）
        from glaucous.spec.pipeline import _strip_fences

        fenced = "```markdown\n## 需求与边界\n目标\n```"
        assert _strip_fences(fenced) == "## 需求与边界\n目标"
        inner = "```\n## 设计\n```bash\ncode\n```\n```"
        assert "```bash" in _strip_fences(inner)  # 正文内围栏不动

    def test_clean_body_strips_preamble_and_meta_tail(self) -> None:
        # 验收实测（2026-08-31）：开场白裁掉 + 尾部交互性元话语段落剔除，正文中间不动
        from glaucous.spec.pipeline import _clean_body

        dirty = (
            "好的，以下是起草的 Spec。\n\n"
            + FULL_BODY.rstrip()
            + "\n\n**起草说明**：以上为完整 Spec 正文。如需调整范围，请直接指出；"
            "确认后可进入评审/执行阶段。"
        )
        cleaned = _clean_body(dirty)
        assert cleaned.startswith("## 需求与边界")
        assert "起草说明" not in cleaned
        assert "确认后可进入" not in cleaned
        assert "## 风险与回退" in cleaned  # 正文节不受影响
        assert "好的，以下是起草的 Spec" not in cleaned

    def test_clean_body_keeps_legit_content(self) -> None:
        from glaucous.spec.pipeline import _clean_body

        assert _clean_body(FULL_BODY).strip() == FULL_BODY.strip()  # 干净正文原样保留
