"""rich 渲染层：工具行 / 四类卡片 / 状态栏 / Banner / 意象文案（任务 3.2，FR-30/31）。

设计要点（Day5 Plan §4.2，概设 §8.3/§8.4）：
- 无框优先：常规操作单行呈现（⏺ 动作行 / ⎿ 结果行），仅人介入时刻
  （方案确认/审批/提问/警示）升格为 Panel 卡片——视觉音量只在需要人介入时升高；
- 固定符号语言：⏺/⎿/❄/✔/✘/◆/⬥ 全程一致；意象文案图标保留意象、文字朴素；
- 事件契约（loop 侧）零改动：本层只消费事件；动态内容一律 markup=False，
  样式只经 theme 命名风格施加（防代码内容误触 markup 解析）。
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..context.budget import BudgetReport
from ..permission.modes import POLICY_PER_ACTION
from ..permission.risk import Risk
from .theme import LEVEL_STYLE, PALETTE

# 结果摘要最多展示的行数（渐进披露：长输出只露尾部摘要）
RESULT_TAIL_LINES = 3

# 状态栏占用条格数（bottom_toolbar 与事件状态行共用）
BAR_WIDTH = 10

# 审批卡 detail 最多展示行数（超长截断标注）
DETAIL_MAX_LINES = 60


class Renderer:
    """事件与交互卡片的统一渲染出口（持 Console；不感知 loop/权限逻辑）。"""

    def __init__(self, console: Console):
        self.console = console
        self.model_name: str = ""       # 状态栏数据源：当前模型档案名（CLI 维护）
        self.last_budget: dict | None = None  # 最近一次 budget 事件 payload

    # -- 事件渲染（承接 Day2~Day4 的 render_event 语义） --------------------

    def render(self, event: str, payload: dict[str, Any], policy: str | None = None,
               mode: str = "") -> None:
        """loop 事件 → 终端呈现。policy 为当前授权策略（状态行 Build 徽标附注用），
        mode 为当前会话模式（budget 状态行携带徽标，与状态栏同款形态）。"""
        if event == "text":
            # 流式正文原样输出（不解析 markup/highlight，Day5 Plan D9）
            self.console.print(payload["text"], end="", markup=False, highlight=False)
        elif event == "diagnostic":
            # 终止诊断：安静单行，不升格卡片
            self.console.print(f"\n  ⎿ {payload['text']}", style="glaucous.dim", markup=False)
        elif event == "mode_changed":
            policy_note = "·每次审批" if payload["policy"] == POLICY_PER_ACTION else "·自动放行"
            self.console.print(
                f"  ◆ {payload['reason']}（{payload['mode']}{policy_note}）",
                style="glaucous.brand", markup=False,
            )
        elif event == "budget":
            self.last_budget = payload
            self.console.print(self._status_text(payload, policy, mode=mode), markup=False)
        elif event == "tool_start":
            call = payload["call"]
            brief = call.arguments if len(call.arguments) <= 80 else call.arguments[:80] + "…"
            line = Text()
            line.append("  ⏺ ", style="glaucous.brand")
            line.append(call.name, style="glaucous.accent")
            if brief:
                line.append(f" {brief}", style="glaucous.text")
            line.append(" ❄", style="glaucous.dim")  # 进行中标记（概设 §8.4 静态形态）
            self.console.print(line)
        elif event == "tool_end":
            result = payload["result"]
            lines = result.content.splitlines()
            if result.ok:
                if len(lines) <= RESULT_TAIL_LINES:
                    summary = " | ".join(lines) if lines else "（无输出）"
                    style = "glaucous.dim"
                else:
                    summary = f"…共 {len(lines)} 行 | " + " | ".join(lines[-RESULT_TAIL_LINES:])
                    style = "glaucous.dim"
            else:
                summary = f"✘ {result.content}"
                style = "glaucous.error"
            self.console.print(f"    ⎿ {summary}", style=style, markup=False)

    # -- 状态栏（FR-31：模式徽标 + 模型 + 上下文占用） ----------------------

    def _mode_badge(self, mode: str, policy: str | None) -> Text:
        if mode == "build":
            note = "·每次审批" if policy == POLICY_PER_ACTION else "·自动放行"
            badge = Text(f"⬥ build{note}", style="glaucous.accent")
        else:
            badge = Text("◆ plan", style="glaucous.brand")
        return badge

    def _status_text(self, budget: dict, policy: str | None, mode: str = "") -> Text:
        """单行状态文本：`  [◆ plan | <模型> | ctx 34% ███████░░░ 43k/128k]`。"""
        used, limit = int(budget["used"]), int(budget["limit"])
        ratio = min(1.0, used / limit) if limit else 0.0
        filled = round(ratio * BAR_WIDTH)
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
        level_style = LEVEL_STYLE.get(budget.get("level", "low"), "glaucous.dim")
        line = Text("  [")
        if mode:
            line.append_text(self._mode_badge(mode, policy))
            line.append(" | ")
        line.append(self.model_name or "-", style="glaucous.dim")
        line.append(f" | ctx {ratio:4.0%} {bar} {used // 1000}k/{limit // 1000}k", style=level_style)
        note = {"warn": "（建议 /compact）", "critical": "（🌊 即将自动压缩）"}.get(budget.get("level"), "")
        if note:
            line.append(note, style=level_style)
        line.append("]")
        return line

    def toolbar_text(self, mode: str, policy: str | None) -> str:
        """bottom_toolbar 用纯文本（prompt_toolkit 侧着色由其样式承担，这里给内容）。"""
        budget = self.last_budget
        badge = "⬥ build" if mode == "build" else "◆ plan"
        if budget is None:
            ctx = "ctx --"
        else:
            used, limit = int(budget["used"]), int(budget["limit"])
            ratio = min(1.0, used / limit) if limit else 0.0
            filled = round(ratio * BAR_WIDTH)
            ctx = f"ctx {ratio:4.0%} {'█' * filled}{'░' * (BAR_WIDTH - filled)} {used // 1000}k/{limit // 1000}k"
        return f"[{badge} | {self.model_name or '-'} | {ctx}]"

    def render_status(self, budget: dict, mode: str, policy: str | None) -> None:
        """任务执行期间的状态行（与 bottom_toolbar 接力实现常驻，Day5 Plan D1）。"""
        self.last_budget = budget
        self.console.print(self._status_text(budget, policy, mode=mode), markup=False)

    def render_budget_report(self, report: BudgetReport, mode: str, policy: str | None) -> None:
        """/compact 等命令后的状态行（budget 模块报告 → 事件同款形态）。"""
        payload = {
            "used": report.used, "limit": report.limit,
            "percent": report.percent, "level": report.level,
        }
        self.render_status(payload, mode, policy)

    # -- Banner 与杂项文案 ---------------------------------------------------

    def banner(self) -> None:
        """启动 Banner（一次，之后不再打扰）。"""
        self.console.print("☁ Glaucous · coding agent", style="glaucous.brand")
        self.console.print("雨过天青，海鸥滑翔，代码自有清凉", style="glaucous.text")
        # 档案名是外部配置内容（可能含 [/] 等），必须禁用 markup（代码评审 r1 B1）
        self.console.print(
            f"当前模型：{self.model_name or '-'} · 输入任务开始对话，/help 查看命令，/exit 退出。",
            style="glaucous.muted", markup=False,
        )

    def retry(self, attempt: int, delay: float) -> None:
        """「↻ 重试中」意象文案（概设 §8.4 意象表；LLMClient.on_retry 注入点）。"""
        self.console.print(f"  ↻ 重试中（第 {attempt} 次，约 {delay:.0f}s）", style="glaucous.muted")

    def note(self, text: str) -> None:
        """普通提示行（海盐青）。"""
        self.console.print(f"  {text}", style="glaucous.dim", markup=False)

    def info(self, text: str) -> None:
        """成功/确认类提示（海草绿）。"""
        self.console.print(f"  ✔ {text}", style="glaucous.success", markup=False)

    def error(self, text: str) -> None:
        """错误/失败类提示（陶土红，单行不升格卡片）。"""
        self.console.print(f"  ✘ {text}", style="glaucous.error", markup=False)

    # -- 四类卡片（仅人介入时刻升格，概设 §8.3「无框优先」） -----------------

    def plan_card(self, plan: str) -> None:
        """方案确认卡（落日橙边——需要人介入的时刻）。选项行由调用方在卡外提示。"""
        self.console.print(Panel(
            Text(plan, no_wrap=False),
            title="◆ 方案已就绪", title_align="left",
            border_style=PALETTE["warn"], padding=(0, 1),
        ))
        self.console.print("  ① 开始构建，每次请求权限", style="glaucous.brand")
        self.console.print("  ② 开始构建，同意所有权限", style="glaucous.brand")
        self.console.print("  ③ 继续讨论一下", style="glaucous.brand")

    def approval_card(self, kind: str, target: str, risk: Risk, detail: str, risk_note: str,
                      dangerous: bool) -> None:
        """审批卡：常规落日橙边；破坏性（DANGEROUS）陶土红边 + ⚠ 前缀。"""
        body = Text()
        body.append(f"操作: {kind} {target}\n", style="glaucous.text")
        if risk_note:
            body.append(f"风险: {risk_note}\n", style="glaucous.error" if dangerous else "glaucous.warn")
        if detail:
            detail_lines = detail.splitlines()
            body.append("\n".join(detail_lines[:DETAIL_MAX_LINES]), style="glaucous.dim")
            if len(detail_lines) > DETAIL_MAX_LINES:
                body.append(f"\n…（详情共 {len(detail_lines)} 行，已截断展示）", style="glaucous.muted")
        title = "⚠ 需要您的确认 · 破坏性操作" if dangerous else "⚠ 需要您的确认"
        border = PALETTE["error"] if dangerous else PALETTE["warn"]
        self.console.print(Panel(body, title=title, title_align="left", border_style=border, padding=(0, 1)))

    def ask_card(self, question: str, options: list[str]) -> None:
        """提问卡（海鸥意象，海盐青边）。"""
        body = Text(question, style="glaucous.text")
        for i, option in enumerate(options, 1):
            body.append(f"\n[{i}] {option}", style="glaucous.brand")
        self.console.print(Panel(body, title="🕊 请教你", title_align="left",
                                 border_style=PALETTE["dim"], padding=(0, 1)))

    def warn_card(self, text: str) -> None:
        """警示卡（陶土红边）：本轮失败与 critical 预警的整段呈现。"""
        self.console.print(Panel(Text(text), title="✘ 注意", title_align="left",
                                 border_style=PALETTE["error"], padding=(0, 1)))
