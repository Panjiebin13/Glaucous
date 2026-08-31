"""Spec 文档模板与评审检查清单（v1.1-M5 任务 5.1，FR-54/55/57；spec §3.2）。"""

from __future__ import annotations

# 必需节标题（起草校验与缺节补写轮的依据，spec §4.2；概设 §7.2 七节）
REQUIRED_SECTIONS: tuple[str, ...] = (
    "## 需求与边界",
    "## 澄清记录",
    "## 约束",
    "## 设计",
    "## 任务清单",
    "## 验收标准",
    "## 风险与回退",
)

SPEC_TEMPLATE = """\
## 需求与边界
<!-- 目标一段话；做什么/明确不做什么 -->

## 澄清记录
<!-- 访谈问答要点（逐条：问题 → 用户答复） -->

## 约束
<!-- 技术/范围/禁区约束，逐条可判定 -->

## 设计
<!-- 方案要点：模块改动、接口、数据流 -->

## 任务清单
- [ ] 任务 1（粒度：一次可独立完成并验证）
- [ ] 任务 2

## 验收标准
- 标准 1（验证方式：如何客观判定达成）
- 标准 2（验证方式：…）

## 风险与回退
<!-- 已识别风险与回退路径（每任务有 checkpoint 可 /rollback） -->
"""

# 起草指令：终答 = 完整正文的格式强约束（决策 5 同源口径；§4.2 缺节补写兜底）
DRAFT_INSTRUCTION = (
    "终答必须是完整的 Spec 正文（Markdown），从「## 需求与边界」开始，"
    "依次包含全部七节，不得包含 frontmatter（--- 围栏）或任何解释性前后缀"
    "（严禁输出「起草说明」「以上为完整正文」「如需调整请指出」等任何面向用户的交互性语句）。"
)

# Spec 评审检查清单（概设 §7.3 字面）
SPEC_REVIEW_CHECKLIST = """\
【评审检查清单（Spec 评审）】
1. 需求边界完整？（做什么/不做什么明确）
2. 验收标准可验证？（每条有客观验证方式）
3. 任务清单覆盖需求且粒度合理？
4. 约束无冲突？
5. 风险与回退已识别？
"""

# 代码评审检查清单（概设 §7.3 字面）
CODE_REVIEW_CHECKLIST = """\
【评审检查清单（代码评审）】
1. 逐条对照验收标准？
2. diff 是否越出任务边界？
3. 是否违反 Spec 约束（如禁改目录）？
4. 测试覆盖？
"""

# 评审报告机器可读契约（决策 5）：首行结论 + 两节分级
REVIEW_CONTRACT = """\
【报告格式契约（必须严格遵守）】
- 首行必须是「评审结论：通过」或「评审结论：不通过」之一；
- 随后两节：「【阻塞级问题】」与「【建议级问题】」，逐条编号列出（无则写「无」）；
- 不得调用 ask_user（评审员只做只读评审，不向用户提问）。
"""

# 验收核验契约（决策 11）：逐条 ✓/✗
ACCEPTANCE_CONTRACT = """\
【核验格式契约（必须严格遵守）】
- 对每条验收标准逐行输出「✓ <标准原文>」或「✗ <标准原文>：<未达成原因>」；
- 末行输出「核验结论：全部达成」或「核验结论：存在未决」（仅由 ✗ 决定）；
- 不得调用 ask_user（核验员只做只读核验，不向用户提问）。
"""

# 结论行前缀（解析容忍全角/半角冒号，决策 5 保守兜底在 pipeline 解析层）
VERDICT_PASS = "评审结论：通过"
VERDICT_FAIL = "评审结论：不通过"


def parse_verdict(report: str) -> bool | None:
    """解析评审结论（决策 5）：前 200 字符内找结论行。

    返回 True/False；找不到 → None（调用方保守判不通过）。
    先查「不通过」再查「通过」——「不通过」含「通过」子串，顺序不可反。
    """
    head = report[:200]
    for marker in (VERDICT_FAIL, VERDICT_FAIL.replace("：", ":")):
        if marker in head:
            return False
    for marker in (VERDICT_PASS, VERDICT_PASS.replace("：", ":")):
        if marker in head:
            return True
    return None


def render_report_card_lines(report: str) -> list[str]:
    """评审报告卡分行（阻塞/建议分节提取，容错：无节标 → 全文单段）。"""
    lines: list[str] = []
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.startswith("【阻塞级问题】"):
            lines.append("⛔ 阻塞级问题")
        elif stripped.startswith("【建议级问题】"):
            lines.append("💡 建议级问题")
        elif stripped:
            lines.append(stripped)
    return lines or ["（评审报告为空）"]
