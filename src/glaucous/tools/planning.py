"""方案提交与回读工具：submit_plan + read_plan（Day 2 任务 0.12 / Day 4 任务 2.7）。

v1.1-M1 重造（概设 v1.1 §4.2，FR-38/39）：
- submit_plan 从「Plan 模式出口协议（三选一）」变为「高风险主动确认通道（二选）」，
  全模式可用：BUILD 下=高风险确认卡；PLAN 下=回 Build 的出口（批准即 FR-39
  「口头确认」，是 Plan→Build 唯一工具面出口，见 spec 决策 D-2）；
- 状态切换收敛在 CLI 闭包（confirm 内 PLAN 下批准才 enter_build），
  本工具不持 SessionState；切换反馈由 loop dispatch 统一出口的 mode_changed
  事件呈现，不经回喂文本（r1-B2 方案 c）；
- **方案轻量锚（Day 4，概设 §5.2）**：提交时方案全文落盘 .glaucous/plans/<id>.md，
  批准/反馈回喂尾部附锚行（路径 + 目标一行 + 未完成任务数）；历史/JSONL 中的全文
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

from .base import ALL_MODES, Tool, ToolResult

# 二选一决策常量（v1.1-M1，spec §4.1；三选一 CHOICE_* 退役）
CHOICE_APPROVE = "approve"      # 批准执行
CHOICE_FEEDBACK = "feedback"    # 修改意见（可附文字）


@dataclass
class PlanDecision:
    """二选一决策结果：choice 为 "approve"/"feedback"；feedback 可附用户修改意见。"""

    choice: str
    feedback: str | None = None


# confirm 回调：入参方案全文，出参用户决策（CLI 实现为「方案卡 + 二选一输入」）
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
    """提交结构化方案并请求用户二选一确认（v1.1：高风险主动确认通道，FR-38）。"""

    name = "submit_plan"
    description = (
        "向用户提交方案并请求批准（高风险任务主动使用：大范围重构/删除文件/"
        "修改配置/涉及 .glaucous 与规则文件时）。"
        "方案应包含：目标（需求复述与边界）、步骤（任务清单）、风险（可能的坑与回退方式），"
        "复杂任务可补充澄清记录与设计要点。用户将二选一：批准执行 / 提出修改意见。"
        "批准后按当前授权策略直接执行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan": {"type": "string", "description": "方案全文（Markdown，含目标/步骤/风险）"},
        },
        "required": ["plan"],
    }
    modes = ALL_MODES

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

        if decision.choice == CHOICE_APPROVE:
            # 状态切换由 CLI 闭包完成（PLAN 下批准才 enter_build）；
            # 切换反馈经 loop 统一出口的 mode_changed 事件呈现（r1-B2 方案 c），
            # 回喂文本不拼接切换附言（ConfirmCallback 契约不变）
            return _reply("用户已批准方案，请按方案执行。")
        # 修改意见：结构化回喂反馈，模型据此修订方案而非原样重提
        feedback = (decision.feedback or "").strip()
        feedback_part = f"用户反馈：{feedback}" if feedback else "用户未附加反馈。"
        return _reply(
            f"用户未批准。{feedback_part}请根据反馈修订后再次提交或调整方案。"
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
