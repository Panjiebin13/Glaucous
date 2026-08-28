"""Plan 模式方案产出工具：submit_plan + read_plan（Day 2 任务 0.12 / Day 4 任务 2.7）。

设计要点（Day2 Plan §4.4，概设 §5.2/§7.4；Day4 Plan §4.7）：
- 仅 Plan 模式声明（modes={"plan"}）——Build 下幻觉调用被执行层回喂；
- 方案模板由 system prompt 引导（目标/澄清/设计/步骤/风险，简单任务轻量产出）；
- confirm 回调呈现三选一（①每次请求权限 ②同意所有权限 ③继续讨论），
  回调内部改 SessionState（闭包注入），决策结构化回喂让模型理解决策；
- **方案轻量锚（Day 4，概设 §5.2）**：提交时方案全文落盘 .glaucous/plans/<id>.md，
  决策回喂尾部附锚行（路径 + 目标一行 + 未完成任务数）；历史/JSONL 中的全文
  由 history.view() 视图变换替换为锚文本（Day4 Plan D11），模型需要细节时
  经 read_plan 按需回读（缺省读最新方案）。
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .base import MODE_PLAN, Tool, ToolResult

# 三选一决策常量（概设 §5.2 授权策略语义）
CHOICE_BUILD_PER_ACTION = "1"
CHOICE_BUILD_AUTO_APPROVE = "2"
CHOICE_KEEP_PLANNING = "3"


@dataclass
class PlanDecision:
    """三选一决策结果：choice 为 "1"/"2"/"3"；③ 可附用户反馈文字。"""

    choice: str
    feedback: str | None = None


# confirm 回调：入参方案全文，出参用户决策（CLI 实现为「打印方案 + 三选一输入」）
ConfirmCallback = Callable[[str], PlanDecision]

# plan_id 白名单净化（防路径注入，与 safety/output_limit.sanitize_call_id 同规则）
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def _extract_goal(plan: str) -> str:
    """提取方案目标行（≤80 字符）：首个以「目标」开头的行，容忍 ## / ** 标记前缀。

    与 context/compactor._extract_goal 同规则（Day4 Plan S-15）。
    """
    for line in plan.splitlines():
        stripped = line.strip().lstrip("#*").strip()
        if stripped.startswith("目标"):
            return stripped.lstrip("目标：: ").strip()[:80]
    return ""


def _task_count(plan: str) -> int:
    """统计方案中的任务条目数（列表项/编号项）：落盘时全部未开始（S-15 取舍）。"""
    return sum(
        1
        for line in plan.splitlines()
        if re.match(r"^\s*(?:[-*]|\d+[.、)])\s+", line)
    )


class SubmitPlanTool(Tool):
    """提交结构化方案并请求用户三选一确认，驱动 Plan→Build 切换。"""

    name = "submit_plan"
    description = (
        "在探索完成、方案明确后调用：向用户提交方案全文并请求确认。"
        "方案应包含：目标（需求复述与边界）、步骤（任务清单）、风险（可能的坑与回退方式），"
        "复杂任务可补充澄清记录与设计要点。用户将三选一："
        "①开始构建·每次请求权限 ②开始构建·同意所有权限 ③继续讨论。"
        "仅 Plan 模式可用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan": {"type": "string", "description": "方案全文（Markdown，含目标/步骤/风险）"},
        },
        "required": ["plan"],
    }
    modes = frozenset({MODE_PLAN})

    def __init__(self, confirm: ConfirmCallback, plans_dir: Path | None = None):
        self._confirm = confirm
        self._plans_dir = plans_dir

    def _persist(self, plan: str) -> Path | None:
        """方案全文落盘 .glaucous/plans/<id>.md；失败返回 None（锚行降级，不阻断）。"""
        if self._plans_dir is None:
            return None
        plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        path = self._plans_dir / f"{plan_id}.md"
        try:
            self._plans_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(plan, encoding="utf-8", newline="\n")
        except OSError:
            return None
        return path

    def _anchor_line(self, path: Path | None) -> str:
        """锚行（概设 §5.2：路径 + 目标一行 + 未完成任务清单数；解析失败则省略细节）。"""
        if path is None:
            return ""
        details = []
        try:
            plan_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            plan_text = ""
        goal = _extract_goal(plan_text) if plan_text else ""
        if goal:
            details.append(f"目标：{goal}")
        if plan_text:
            count = _task_count(plan_text)
            if count:
                details.append(f"未完成任务 {count} 项")
        body = "（" + "；".join(details) + "）" if details else ""
        return f"方案已就绪：.glaucous/plans/{path.name}{body}，可用 read_plan 回读全文。"

    async def execute(self, plan: str = "", **_: Any) -> ToolResult:
        if not plan.strip():
            return ToolResult(ok=False, content="plan 不能为空，请产出完整方案后再次提交。")

        saved_path = self._persist(plan)
        anchor = self._anchor_line(saved_path)
        decision = self._confirm(plan)

        def _reply(text: str) -> ToolResult:
            # 锚行附在决策回喂尾部：历史经 L1 保留锚行（_anchor 标记），
            # Build 执行期间「方案在哪、目标是什么」始终可达
            return ToolResult(ok=True, content=f"{text}{('　' + anchor) if anchor else ''}")

        if decision.choice == CHOICE_BUILD_PER_ACTION:
            # 决策回喂文案与 SessionState.enter_build 的调用方（CLI 闭包）约定一致
            return _reply("用户选择①：开始构建（每次请求权限），已切换 Build 模式。请按方案步骤执行，写操作将逐项请求用户确认。")
        if decision.choice == CHOICE_BUILD_AUTO_APPROVE:
            return _reply("用户选择②：开始构建（同意所有权限），已切换 Build 模式。请按方案步骤执行，写操作将自动放行。")
        # ③ 继续讨论：结构化回喂反馈，模型据此修订方案而非原样重提
        feedback = (decision.feedback or "").strip()
        feedback_part = f"用户反馈：{feedback}" if feedback else "用户未附加反馈。"
        return _reply(
            f"用户选择③：继续讨论，仍处于 Plan 模式。{feedback_part}请根据反馈修订方案后再次提交。"
        )


class ReadPlanTool(Tool):
    """回读方案文档全文（方案轻量锚的回取通道，概设 §5.2）。"""

    name = "read_plan"
    description = (
        "回读方案（Plan Document）全文：缺省读取最新方案，也可指定方案 ID。"
        "方案全文不常驻上下文，需要确认方案细节/步骤时调用本工具回读。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "方案 ID（锚行路径中的文件名主体；省略时读最新方案）",
            },
        },
        "required": [],
    }

    def __init__(self, plans_dir: Path):
        self._plans_dir = plans_dir

    async def execute(self, plan_id: str = "", **_: Any) -> ToolResult:
        try:
            if plan_id.strip():
                path = self._plans_dir / f"{_SAFE_ID.sub('_', plan_id.strip())}.md"
                if not path.is_file():
                    return ToolResult(ok=False, content=f"未找到方案 {plan_id}。")
            else:
                files = sorted(self._plans_dir.glob("*.md")) if self._plans_dir.is_dir() else []
                if not files:
                    return ToolResult(ok=False, content="尚无已落盘的方案（本轮会话未提交过方案）。")
                path = files[-1]
            return ToolResult(
                ok=True,
                content=f"方案文件：.glaucous/plans/{path.name}\n" + path.read_text(encoding="utf-8", errors="replace"),
            )
        except OSError as exc:
            return ToolResult(ok=False, content=f"读取方案失败：{exc}")
