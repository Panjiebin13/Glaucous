"""Spec 全流程编排（v1.1-M5 任务 5.2~5.5，FR-53~58；spec §3.3）。

命令式流水线（决策 1）：/spec 命令进入，单命令处理器内串行推进——
澄清→起草→评审循环→批准→执行管线→代码评审循环→验收；状态经
frontmatter 落盘，中断后 /spec 可续跑（仅 executing，决策 12）。

主 agent 动作（澄清/起草/修订/任务执行/修复）经 hooks.run_turn
（= ctx.loop.run）复用主 loop；评审/验收经 hooks.run_review
（= ctx.subagent_runner 直调，决策 7，不经 spawn_agent 工具）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable
import re

from . import templates as T
from .store import SpecDoc, SpecStateError, SpecStore

if TYPE_CHECKING:
    from ..commands import ReplContext

# 轮次上限（决策 9：硬编码 3，不接配置管道；概设 §10 [review].max_rounds 为后续增强）
MAX_ROUNDS = 3
_CLARIFY_MAX = 3
_APPROVE_REV_MAX = 3

_OPT_DRAFT = "进入起草"
_OPT_CLARIFY_MORE = "继续澄清"
_OPT_CANCEL = "取消归档"
_OPT_AUTO = "全自动"
_OPT_DEEP = "深度介入（每轮可提建议）"
_OPT_APPROVE = "批准执行"
_OPT_FEEDBACK = "提修改意见"
_OPT_ARCHIVE = "归档放弃"
_OPT_RETRY = "重试"
_OPT_SKIP = "跳过该任务"
_OPT_ABORT = "归档中止"


@dataclass
class PipelineHooks:
    """测试注入点（任务 5.7 回放的前提，spec §3.3）。"""

    run_turn: Callable[[str], Awaitable[str]]
    # (task, context) → (报告全文, metadata)；metadata["ok"]=False = 子任务本身失败
    run_review: Callable[[str, str], Awaitable[tuple[str, dict[str, Any]]]]
    # 同步 AskCallback 经薄包装（决策 16）；None 语义见决策 13
    ask: Callable[[str, list[str]], Awaitable[str | None]]
    # = store.create(标签,…).seq；不可用 → None。调用点：入口基线 1 次 + 每任务前 1 次
    checkpoint: Callable[[str], int | None]


class SpecPipeline:
    """Spec 子系统编排器（每次 /spec 调用新建实例；状态全在文档侧）。"""

    def __init__(self, ctx: "ReplContext", hooks: PipelineHooks | None = None):
        self._ctx = ctx
        self._store = SpecStore(ctx.workspace)
        self._hooks = hooks or self._default_hooks()
        self._passed_clean = False  # 评审循环正常轮次通过（非升级/失败兜底路径）

    # -- 缺省装配（真实接线；测试以 PipelineHooks 全量替换） -------------------

    def _default_hooks(self) -> PipelineHooks:
        ctx = self._ctx

        async def run_turn(message: str) -> str:
            if ctx.loop is None:
                raise SpecStateError("主循环未装配，无法执行 Spec 流程")
            from ..ui.callbacks import run_managed_turn

            # R1/R3：repl 同款轮壳（思考区收缩 + 终答 md 卡片）
            # v1.1 评审重构：自 cli 迁入 ui.callbacks——spec 子系统不再反向依赖入口模块 cli
            return await run_managed_turn(ctx, message, label=message[:40])

        async def run_review(task: str, context: str) -> tuple[str, dict[str, Any]]:
            runner = getattr(ctx, "subagent_runner", None)
            if runner is None:
                return "", {"ok": False}
            from ..ui.callbacks import thinking_enter, thinking_exit

            # R5：子评审区间段自有干净思考区生命周期（防旧计数/正文尾泄漏）
            thinking_enter(ctx)
            try:
                result = await runner.run(task, context)
                if not result.ok:
                    return result.content or "", {"ok": False}
                report = result.content
                metadata = dict(result.metadata or {})
                # 超 1000 字截断的回读（决策 6）：完整报告在 outputs/ 归档
                agent_id = metadata.get("sub_agent")
                if "报告已截断" in report and agent_id and ctx.outputs_dir is not None:
                    archive = ctx.outputs_dir / f"spawn_agent-{agent_id}.log"
                    try:
                        report = archive.read_text(encoding="utf-8")
                    except OSError:
                        pass  # 归档缺失 → 截断文本继续（尽力而为）
                metadata["ok"] = True
                return report, metadata
            finally:
                thinking_exit(ctx)

        from ..ui.callbacks import make_ask_callback

        sync_ask = make_ask_callback(ctx)

        async def ask(question: str, options: list[str]) -> str | None:
            return sync_ask(question, options)

        def checkpoint(label: str) -> int | None:
            store = ctx.checkpoint_store
            if store is None:
                return None
            cp = store.create(label, len(ctx.history.messages))
            return cp.seq if cp is not None else None

        return PipelineHooks(run_turn=run_turn, run_review=run_review, ask=ask, checkpoint=checkpoint)

    # -- 渲染助手（经 ctx.renderer：测试用 FakeRenderer 可断言） --------------

    def _note(self, text: str) -> None:
        self._ctx.renderer.note(text)

    def _card(self, title: str, lines: list[str]) -> None:
        self._ctx.renderer.info(f"◆ {title}")
        for line in lines:
            self._note(f"  {line}")

    # -- 顶层入口 --------------------------------------------------------------

    async def start(self, requirement: str) -> None:
        """/spec <需求>：全流程（决策 12：顶层捕获，状态停驻不击穿 REPL）。"""
        doc: SpecDoc | None = None
        try:
            goal = await self._clarify(requirement)
            if goal is None:
                return  # 澄清阶段取消：尚无文档，无需归档
            body = await self._draft(requirement, goal)
            doc = self._store.create(goal, body)
            self._note(f"Spec 已起草：.glaucous/specs/{doc.spec_id}.md")
            self._store.transition(doc, "reviewing")
            if not await self._review_loop(doc):
                return
            # R2（用户验收反馈）：全自动模式且评审正常轮次通过（无阻塞）→
            # 跳过批准卡直接执行；深度介入或升级/兜底路径仍呈批准卡。
            if doc.meta.get("mode") == "auto" and self._passed_clean:
                self._store.transition(
                    doc, "approved", approved_at=datetime.now().isoformat(timespec="seconds")
                )
                self._note("评审通过且无阻塞项 · 全自动模式：跳过批准卡，直接进入执行。")
            elif not await self._approve(doc):
                return
            await self._enter_execution(doc)
        except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 —— 决策 12
            self._ctx.renderer.error(
                f"Spec 流程中断（{exc.__class__.__name__}：{exc}）。"
                + (f"状态停驻于 {doc.status}（{doc.spec_id}），" if doc else "")
                + "可用 /spec 续跑或 /spec cancel 归档。"
            )

    async def resume(self, doc: SpecDoc) -> None:
        """/spec 无参续跑：仅 executing 可恢复（决策 12）。"""
        try:
            await self._execute(doc)
            await self._code_review_phase(doc)
            await self._final_summary(doc)
        except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001
            self._ctx.renderer.error(
                f"Spec 续跑中断（{exc.__class__.__name__}：{exc}）。"
                f"状态停驻于 {doc.status}（{doc.spec_id}），可再次 /spec 续跑。"
            )

    async def cancel(self, doc: SpecDoc) -> None:
        """/spec cancel：确认 → archived + 取消备注（FR-59；B2：同 start/resume 顶层捕获，
        非法迁移/IO 失败报错提示不击穿 REPL）。"""
        try:
            choice = await self._hooks.ask(
                f"确认终止 Spec「{doc.name}」（{doc.status}）并归档？", ["确认归档", "放弃操作"]
            )
            if choice != "确认归档":  # None → 停驻（决策 13）
                self._note("已取消，Spec 状态不变。")
                return
            self._store.append_note(doc, "用户经 /spec cancel 终止流程")
            self._store.transition(doc, "archived")
            self._note(f"Spec {doc.spec_id} 已归档。")
        except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 —— 决策 12 同款
            self._ctx.renderer.error(f"取消失败（{exc.__class__.__name__}：{exc}），Spec 状态不变。")

    async def ask_continue(self, doc: SpecDoc) -> None:
        """executing 中断后的续跑询问（公开方法，命令层消费，S3）。"""
        choice = await self._hooks.ask("如何处理？", ["继续执行", "取消归档"])
        if choice == "继续执行":
            await self.resume(doc)
        elif choice == "取消归档":
            await self.cancel(doc)
        # None → 停驻（决策 13）

    # -- 澄清（FR-53） ---------------------------------------------------------

    async def _clarify(self, requirement: str) -> str | None:
        """澄清访谈（≤3 轮）→ 目标一行；取消 → None（尚无文档）。"""
        prev_answer = ""
        for round_no in range(1, _CLARIFY_MAX + 1):
            message = (
                f"[Spec 流程·澄清] 需求原文：{requirement}\n"
                + (f"上一轮澄清产出：{prev_answer}\n" if prev_answer else "")
                + "请用 ask_user 逐点澄清关键决策点（目标边界/输入输出/约束/验收预期），"
                "用户表示清楚后，以一行「澄清完成：<目标一行>」作为终答。"
            )
            answer = await self._hooks.run_turn(message)
            prev_answer = answer
            choice = await self._hooks.ask(
                "需求是否已澄清、可进入起草？", [_OPT_DRAFT, _OPT_CLARIFY_MORE, _OPT_CANCEL]
            )
            if choice == _OPT_DRAFT:
                return self._goal_of(answer, requirement)
            if choice != _OPT_CLARIFY_MORE:  # None/取消 → 归档语义（决策 13）
                self._note("已取消，未生成 Spec。")
                return None
        # 耗尽升级（决策 9/13：None → 取消）
        choice = await self._hooks.ask("澄清已达 3 轮，如何处理？", ["仍要起草", "取消"])
        if choice == "仍要起草":
            return self._goal_of(prev_answer, requirement)
        self._note("已取消，未生成 Spec。")
        return None

    @staticmethod
    def _goal_of(answer: str, requirement: str) -> str:
        for line in answer.splitlines():
            if "澄清完成" in line:
                # S6：从「澄清完成」标记后截取，只切第一个冒号（全角优先），
                # 避免目标行内含半角冒号被二次截断
                tail = line.split("澄清完成", 1)[-1].lstrip()
                for sep in ("：", ":"):
                    if tail.startswith(sep):
                        tail = tail[len(sep):]
                        break
                return tail.strip()[:40] or requirement[:40]
        return requirement.strip()[:40] or "未命名 Spec"

    # -- 起草（FR-54） ---------------------------------------------------------

    async def _draft(self, requirement: str, goal: str) -> str:
        message = (
            f"[Spec 流程·起草] 需求（澄清后）：{goal}\n需求原文：{requirement}\n"
            "请起草完整 Spec（本会话中的澄清问答请整理进「## 澄清记录」）。模板：\n"
            f"{T.SPEC_TEMPLATE}\n{T.DRAFT_INSTRUCTION}"
        )
        body = _clean_body(_strip_fences(await self._hooks.run_turn(message)))
        missing = [s for s in T.REQUIRED_SECTIONS if s not in body]
        if missing:
            fill = _clean_body(_strip_fences(await self._hooks.run_turn(
                f"[Spec 流程·补写] 你起草的 Spec 缺少节：{'、'.join(missing)}。"
                f"请输出补全后的完整正文。\n当前正文：\n{body}\n{T.DRAFT_INSTRUCTION}"
            )))
            still_missing = [s for s in T.REQUIRED_SECTIONS if s not in fill]
            if not still_missing:
                body = fill
            else:
                note = "- 模板节缺失（起草补写未成功）：" + "、".join(still_missing)
                body = body.rstrip() + (
                    f"\n\n## 风险与回退\n{note}\n" if "## 风险与回退" not in body else f"\n{note}\n"
                )
        return body.strip() + "\n"

    # -- Spec 评审循环（FR-55） -------------------------------------------------

    async def _review_loop(self, doc: SpecDoc) -> bool:
        """≤3 轮评审；返回 False = 用户取消归档。"""
        mode = await self._hooks.ask("评审循环模式？", [_OPT_AUTO, _OPT_DEEP])
        deep = mode == _OPT_DEEP  # None → 全自动（决策 13）
        doc.meta["mode"] = "deep" if deep else "auto"
        feedback = ""
        for round_no in range(1, MAX_ROUNDS + 1):
            doc.meta["round"] = round_no
            passed, ok = await self._review_round(doc, T.SPEC_REVIEW_CHECKLIST, feedback, title="Spec 评审")
            if not ok:
                return await self._review_failure_recovery(doc)
            if passed:
                self._passed_clean = True  # R2：正常轮次通过 → 全自动可免批准卡
                self._store.save_body(doc, doc.body)  # round/mode 落盘
                return True
            if deep:  # S2：每轮报告卡后均收建议（含第 3 轮，建议随升级环节修订注入）
                suggestion = await self._hooks.ask("请对本轮修订提一句建议（可留空）", [])
                feedback = (suggestion or "").strip()  # None → 空建议继续（决策 13）
            if round_no < MAX_ROUNDS:
                doc.body = await self._revise_turn(doc, feedback)
                self._store.save_body(doc, doc.body)
        # 耗尽升级（决策 9；None → 最保守 = 取消归档）
        choice = await self._hooks.ask(
            "评审已 3 轮未通过，如何处理？", ["呈请批准（带未决阻塞）", "再修订一轮（仅一次）", _OPT_CANCEL]
        )
        if choice == "再修订一轮（仅一次）":
            doc.body = await self._revise_turn(doc, feedback)
            self._store.save_body(doc, doc.body)
            passed, _ok = await self._review_round(doc, T.SPEC_REVIEW_CHECKLIST, feedback, title="Spec 评审（加轮）")
            self._note("加轮评审结束，进入批准环节（未决项将随批准卡呈现）。" if not passed else "加轮评审通过。")
            return True
        if choice != "呈请批准（带未决阻塞）":
            self._store.append_note(doc, "评审轮次耗尽，用户取消")
            self._store.transition(doc, "archived")
            return False
        return True

    async def _review_round(
        self, doc: SpecDoc, checklist: str, feedback: str, title: str, extra_context: str = ""
    ) -> tuple[bool, bool]:
        """单轮评审：返回 (是否通过, 子任务是否正常)。"""
        context = (
            f"{checklist}\n{T.REVIEW_CONTRACT}\n"
            + (f"【用户本轮反馈】\n{feedback}\n" if feedback else "")
            + extra_context
            + f"\n【Spec 全文（{doc.spec_id}）】\n{_render_doc(doc)}"
        )
        report, metadata = await self._hooks.run_review(
            f"你是 Spec 评审员。请对任务上下文中的 Spec 全文按检查清单评审并输出评审报告。{title}。",
            context,
        )
        if not metadata.get("ok"):
            return False, False
        verdict = T.parse_verdict(report)
        passed = verdict is True  # None → 保守判不通过（决策 5）
        if verdict is None:
            report = f"（报告契约解析失败，保守判不通过）\n{report}"
        self._card(f"{title}报告（第 {doc.meta.get('round', 1)} 轮）· {'通过' if passed else '不通过'}",
                   T.render_report_card_lines(report)[:60])
        self._last_report = report
        return passed, True

    async def _review_failure_recovery(self, doc: SpecDoc) -> bool:
        """子评审任务本身失败（§五）：重试/跳过呈批/取消；返回 False = 取消。"""
        choice = await self._hooks.ask(
            "评审子任务执行失败，如何处理？", ["重试本轮", "跳过评审直接呈批", _OPT_CANCEL]
        )
        if choice == "重试本轮":
            # S1 作者裁决：与代码评审侧重试语义对齐——重试结果参与判定，
            # 通过则直接进批准链，不通过仍回原耗尽升级路径（本函数已在升级分支内，
            # 此处重试不通过则视为呈批，避免无限拉锯）
            passed, _ok = await self._review_round(doc, T.SPEC_REVIEW_CHECKLIST, "", title="Spec 评审（重试）")
            if not passed:
                self._note("重试仍未通过，进入批准环节（未决阻塞将随批准卡呈现）。")
            return True
        if choice == "跳过评审直接呈批":
            self._note("已跳过评审（评审子任务失败），批准卡将提示评审缺失。")
            return True
        self._store.append_note(doc, "评审子任务失败，用户取消")
        self._store.transition(doc, "archived")
        return False

    async def _revise_turn(self, doc: SpecDoc, feedback: str) -> str:
        report = getattr(self, "_last_report", "")
        message = (
            "[Spec 流程·修订] 请根据评审发现修订 Spec，只输出修订后的完整正文。\n"
            f"评审发现：\n{report}\n"
            + (f"用户建议：{feedback}\n" if feedback else "")
            + f"当前 Spec 正文：\n{doc.body}\n{T.DRAFT_INSTRUCTION}"
        )
        return _clean_body(_strip_fences(await self._hooks.run_turn(message))) + "\n"

    # -- 批准（FR-56） ---------------------------------------------------------

    async def _approve(self, doc: SpecDoc) -> bool:
        for attempt in range(1, _APPROVE_REV_MAX + 1):
            tasks = doc.tasks()  # 每轮重算（修订可能改动任务清单）
            self._card(
                f"Spec 批准卡（{doc.spec_id}）",
                [
                    f"目标：{doc.name}",
                    f"任务 {len(tasks)} 项 · 验收标准 {len(doc.acceptance())} 条 · 评审轮次 {doc.meta.get('round', 0)}",
                    f"文档：.glaucous/specs/{doc.spec_id}.md（read_spec 可回读全文）",
                ],
            )
            choice = await self._hooks.ask("是否批准执行该 Spec？", [_OPT_APPROVE, _OPT_FEEDBACK, _OPT_ARCHIVE])
            if choice == _OPT_APPROVE:
                self._store.transition(
                    doc, "approved", approved_at=datetime.now().isoformat(timespec="seconds")
                )
                return True
            if choice == _OPT_ARCHIVE:
                self._store.transition(doc, "archived")
                self._note("Spec 已归档（用户放弃）。")
                return False
            # None 或 提修改意见：收集意见；空意见按归档（决策 13）
            feedback = await self._hooks.ask("请输入修改意见", [])
            if not (feedback or "").strip():
                self._store.append_note(doc, "批准环节用户未提供意见，归档")
                self._store.transition(doc, "archived")
                return False
            doc.body = await self._revise_turn(doc, feedback.strip())
            self._store.save_body(doc, doc.body)
        choice = await self._hooks.ask("批准修订已 3 轮，如何处理？", ["仍要批准", "归档"])
        if choice == "仍要批准":
            self._store.transition(doc, "approved", approved_at=datetime.now().isoformat(timespec="seconds"))
            return True
        self._store.transition(doc, "archived")
        return False

    # -- 执行管线（FR-56） -------------------------------------------------------

    async def _enter_execution(self, doc: SpecDoc) -> None:
        entry_seq = self._hooks.checkpoint(f"Spec {doc.spec_id} 执行入口")  # 决策 3 基线
        if entry_seq is None:
            self._note("checkpoint 不可用（非 Git 工作区）：执行期不打快照，代码评审将无 diff。")
        self._store.transition(doc, "executing", entry_checkpoint=entry_seq)
        await self._execute(doc)
        await self._code_review_phase(doc)
        await self._final_summary(doc)

    async def _final_summary(self, doc: SpecDoc) -> None:
        """R3（用户验收反馈）：全部任务完成后主 agent 简要汇报做了什么，
        经受管轮壳以 md 卡片呈现（最终回答）。失败不致命：流程已到终态。"""
        message = (
            f"[Spec 流程·总结] Spec {doc.spec_id}（{doc.name}）已到达终态：{doc.status}。"
            "请向用户简要汇报本次 Spec 执行做了哪些工作：要点式 3~6 行（完成了什么、"
            "关键验证结果、遗留事项），不要罗列逐步过程细节。"
        )
        try:
            await self._hooks.run_turn(message)
        except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 —— 总结尽力而为，不击穿收尾
            self._note(f"（总结轮未生成：{exc}，可用 /spec status 查看结果）")

    async def _execute(self, doc: SpecDoc) -> None:
        """逐任务：权威任务级快照（决策 2）→ loop.run → 勾选写回（决策 4）。"""
        pending = [(no, text) for no, done, text in doc.tasks() if not done]
        total = len(pending)
        for i, (task_no, text) in enumerate(pending, 1):
            self._ctx.renderer.info(f"▶ 任务 {i}/{total}：{text}")
            self._hooks.checkpoint(f"Spec {doc.spec_id} 任务 {task_no}：{text[:40]}")  # None 容忍
            done_ok = False
            while True:
                try:
                    await self._hooks.run_turn(self._task_prompt(doc, task_no, text, total - i))
                    done_ok = True
                    break
                except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 —— 任务级兜底
                    choice = await self._hooks.ask(
                        f"任务 {task_no} 执行失败（{exc}），如何处理？", [_OPT_RETRY, _OPT_SKIP, _OPT_ABORT]
                    )
                    if choice == _OPT_RETRY:
                        continue  # 重试不重复打快照（§4.4）
                    if choice == _OPT_SKIP:
                        self._store.append_note(doc, f"任务 {task_no} 跳过：{text}（{exc}）")
                        break
                    # None / 归档中止 → 停驻归档（决策 13）
                    self._store.append_note(doc, f"任务 {task_no} 失败，用户中止：{exc}")
                    self._store.transition(doc, "archived")
                    raise SpecStateError("执行中止（用户归档）") from exc
            if done_ok:
                self._store.check_task(doc, task_no)
        self._note(f"任务清单执行完毕（{total} 项），进入代码评审循环。")

    def _task_prompt(self, doc: SpecDoc, task_no: int, text: str, remaining: int) -> str:
        return (
            f"[Spec 执行·任务 {task_no}] {text}\n"
            f"Spec 已就绪：.glaucous/specs/{doc.spec_id}.md（{doc.name} · 未完成 {remaining + 1} 项），"
            "read_spec 可回读全文。\n约束：仅完成本任务，不越界；完成后简述结果。"
        )

    # -- 代码评审循环（FR-57）与验收（FR-58） -------------------------------------

    async def _code_review_phase(self, doc: SpecDoc) -> None:
        self._store.transition(doc, "code_review")
        diff_text, diff_note = self._diff_summary(doc)
        extra = (
            f"\n【代码变更（自执行入口快照）】\n{diff_text}\n"
            if diff_text else f"\n【代码变更】{diff_note}\n"
        )
        for round_no in range(1, MAX_ROUNDS + 1):
            doc.meta["round"] = round_no
            while True:  # 子任务故障重试不消耗轮次（§五）
                passed, ok = await self._review_round(
                    doc, T.CODE_REVIEW_CHECKLIST, "", title="代码评审", extra_context=extra
                )
                if ok:
                    break
                choice = await self._hooks.ask(
                    "代码评审子任务执行失败，如何处理？", ["重试本轮", "按现状出验收报告", _OPT_CANCEL]
                )
                if choice == "重试本轮":
                    continue
                if choice == "按现状出验收报告":
                    passed = True  # 强制出环进入验收（验收环节仍会逐条核验）
                    break
                self._store.append_note(doc, "代码评审子任务失败，用户取消")
                self._store.transition(doc, "archived")
                return
            if passed:
                break
            if round_no < MAX_ROUNDS:
                fix_message = (
                    "[Spec 执行·修复] 代码评审发现问题，请修复后简述改动。\n"
                    f"评审发现：\n{getattr(self, '_last_report', '')}\n"
                    "Spec 验收标准：\n" + "\n".join(f"- {a}" for a in doc.acceptance())
                )
                await self._hooks.run_turn(fix_message)
        else:
            choice = await self._hooks.ask(
                "代码评审已 3 轮未通过，如何处理？",
                ["按现状出验收报告", "再修复一轮（仅一次）", _OPT_CANCEL],
            )
            if choice == "再修复一轮（仅一次）":
                await self._hooks.run_turn(
                    "[Spec 执行·修复] 请根据最近一轮评审发现修复，完成后简述改动。\n"
                    f"评审发现：\n{getattr(self, '_last_report', '')}"
                )
                # 修复后复审一次（§4.5「复审」字面）：结果仅记入卡片，验收环节照常逐条核验
                await self._review_round(
                    doc, T.CODE_REVIEW_CHECKLIST, "", title="代码评审（加轮）", extra_context=extra
                )
            elif choice != "按现状出验收报告":  # None/取消 → 归档（决策 13）
                self._store.append_note(doc, "代码评审轮次耗尽，用户取消")
                self._store.transition(doc, "archived")
                return
        await self._acceptance(doc, extra)

    def _diff_summary(self, doc: SpecDoc) -> tuple[str, str]:
        """基线快照 → 变更清单摘要（§4.5，r1-S11：经 store 既有封装）。"""
        seq = doc.meta.get("entry_checkpoint")
        store = getattr(self._ctx, "checkpoint_store", None)
        if seq is None or store is None or not store.available:
            return "", "无 diff（非 Git 工作区，未创建执行入口快照）"
        cp = store.get(int(seq))
        if cp is None:
            return "", "无 diff（执行入口基线快照已被淘汰）"
        try:
            changes = store.preview_changes(cp)
        except Exception:  # noqa: BLE001 —— diff 失败降级，不阻断评审
            return "", "无 diff（变更清单生成失败）"
        lines = [f"- {c.get('status', '?')} {c.get('path', '')}" for c in changes]
        text = "\n".join(lines) if lines else "（无文件变更）"
        if len(text) > 4000:
            text = text[:4000] + f"\n…共 {len(lines)} 项变更，已截断"
        return text, ""

    async def _acceptance(self, doc: SpecDoc, extra_context: str) -> None:
        """验收核验（决策 11）：逐条 ✓/✗ → verified / archived。"""
        criteria = doc.acceptance()
        context = (
            f"{T.ACCEPTANCE_CONTRACT}\n【验收标准（逐条核验）】\n"
            + "\n".join(f"- {a}" for a in criteria)
            + f"\n【任务完成情况】\n"
            + "\n".join(f"- [{'x' if done else ' '}] {text}" for _no, done, text in doc.tasks())
            + extra_context
        )
        report, metadata = await self._hooks.run_review(
            "你是验收核验员。请对任务上下文中的验收标准逐条核验并输出核验报告。",
            context,
        )
        lines = report.splitlines() if metadata.get("ok") else ["（核验子任务失败，按存在未决处理）"]
        # 实测修正（用户验收 2026-08-31）：核验行常以列表标记开头（「- ✓ 标准…」），
        # 先剥首层列表标记再判 ✓/✗，否则全被判「存在未决」
        norm = [
            line.strip()[1:].strip() if line.strip()[:1] in ("-", "*") else line.strip()
            for line in lines
        ]
        failed = [s for s in norm if s.startswith("✗")]
        checked = [s for s in norm if s.startswith("✓")]
        self._card(f"验收报告（{doc.spec_id}）", [line.strip() for line in lines if line.strip()][:60])
        # B3 作者裁决：决策 11 保守口径——需「全部标准有 ✓ 且无 ✗」才 verified；
        # 契约违约（无 ✓ 行/子任务失败）与评审环节决策 5 同口径，不判 verified。
        # 另需 ✓ 行数 ≥ 标准数（防只核验部分就放行）
        all_ok = metadata.get("ok") and criteria and not failed and len(checked) >= len(criteria)
        result = "全部达成" if all_ok else "存在未决"
        doc.meta["acceptance"] = result  # S5：/spec status 卡数据源（随 transition 落盘）
        if all_ok:
            self._store.transition(doc, "verified")
            self._note(f"Spec {doc.spec_id} 验收通过，状态 verified。")
        else:
            self._store.append_note(
                doc, "验收存在未决：" + ("；".join(failed[:5]) or "核验未完成（契约违约或子任务失败）")
            )
            self._store.transition(doc, "archived")
            self._note(f"Spec {doc.spec_id} 归档（存在未决项），详见文档「风险与回退」。")


def _render_doc(doc: SpecDoc) -> str:
    """评审输入用的文档形态：元信息摘要 + 正文。"""
    return f"（名称：{doc.name} · 状态：{doc.status} · 轮次：{doc.meta.get('round', 0)}）\n{doc.body}"


def _strip_fences(text: str) -> str:
    """剥离模型终答可能包裹的 markdown 代码围栏（健壮性：起草/修订/补写共用）。

    真实模型常把全文包在 ```markdown ... ``` 里；不剥离则节标题校验失败、
    评审输入带围栏噪声。只剥首尾围栏，正文内的围栏（代码块）不动。
    """
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


# 交互性元话语标记（实测：模型违反「不得含解释性前后缀」契约，
# 在终答尾部附「起草说明：以上为完整 Spec 正文…确认后可进入…」）
_META_MARKERS = ("起草说明", "以上为完整", "请直接指出", "确认后可进入", "如需调整")


def _clean_body(body: str) -> str:
    """起草/修订终答净化（验收实测 2026-08-31）：
    ① 裁掉首个必需节标题之前的开场白；② 尾部含 ≥2 个元话语标记的段落剔除，
    防交互性文字混入 Spec 正文（污染评审输入与文档）。正文中间内容不动。"""
    lines = body.splitlines()
    start = next(
        (i for i, ln in enumerate(lines)
         if any(ln.strip().startswith(s) for s in T.REQUIRED_SECTIONS)),
        None,
    )
    if start:
        lines = lines[start:]
    blocks = re.split(r"\n\s*\n", "\n".join(lines))
    while blocks and sum(1 for m in _META_MARKERS if m in blocks[-1]) >= 2:
        blocks.pop()
    return "\n\n".join(blocks).strip()
