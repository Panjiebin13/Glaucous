# 代码评审报告：V1.1-M2 多 Agent 基础（第 3 轮·聚焦复审）

> 评审日期：2026-08-30 17:34
> 评审对象：spec docs/designs/202608301500-plan-v11-m2-subagent.md；聚焦改动 src/glaucous/cli.py（confirm 闭包归属行时序 / render_event 与 make_on_event 的 [child-N] markup escape / make_on_event child_note 去重）
> 模式：聚焦复审（r2 报告 B1 修复 + r2-S1/r2-S2 顺带修复；波及面：ThinkingView 擦除协议时序、make_on_event 闭包生命周期、render_event /expand 重放、session_events 落账、测试覆盖）
> 结论：**不通过**（阻塞 1 项，建议 2 项）

## 一、阻塞问题

### B1. r2-S2 顺带修复未生效：child_note 声明在 on_event 回调体内，每次事件调用重建空列表，去重永不触发——子 text 增量仍逐条计 N/刷同文行

- **维度**：逻辑正确性（声明的修复行为未落地）
- **代码位置**：src/glaucous/cli.py:1082-1083——

```python
def on_event(event: str, payload: dict[str, Any]) -> None:
    child_note: list[str] = []  # 子正文摘要行去重（同 agent 连续增量只计一次，r2-S2）
```

  child_note 声明于 on_event 函数体第一行（L1083），而非 make_on_event 闭包层（L1072 函数体、L1082 def 之外）。每次事件回调都重建空列表，L1112 `first_note = not child_note or child_note[0] != agent` 的 `not child_note` 恒为 True——first_note 恒真，L1113-1121 的 thinking.add / 降级直打对每条增量照常触发。
- **声明原文**（本轮修复说明）：「cli.py make_on_event 增 child_note 去重——同 agent 连续 text 增量只在首条触发 thinking.add/降级直打……session_events 落账照常」；r2-S2 修复方向原文：「仅首条记行、后续增量只落账不进窗口」。
- **实证**（WSL glaucous 环境，rich 输出捕获，50 个同 agent（child-1）连续 sub_event text 增量；交替组为 child-1,child-1,child-2,child-2）：

```
A_direct_child1_lines: 50 A_recorded: 50      ← 声明预期直打 1 行（实际 50）；落账 50 ✓
C_thinking_add: 50                             ← 声明预期 thinking.add 恰 1 次（实际 50，N 口径灌水依旧）
B_alt_c1: 2 B_alt_c2: 2                        ← 交替 4 事件全打（去重语义预期 1/1）
```

  r2-S2 指出的两个症状（N 被增量灌水、滚动区同文行刷屏）依旧存在，行为与修复前一致；「N 不含正文增量」对齐目标未达成。注：tests/test_subagent.py 无去重断言（见 S2），205 passed 与本失效并存。
- **修复方向**：将 `child_note: list[str] = []` 上移至 make_on_event 函数体内、`def on_event` 之前（闭包捕获，跨调用保持；rebuild_loop 重建回调实例时随之重建，/clear、/resume 后无残留）。现有判定式在正确作用域下即满足声明语义：同 agent 连续增量只触发一次，不同 agent（串行派发 child-1→child-2）交替时各打首条。改动一行位置，随后 r4 聚焦复审该点。
## 二、建议问题

### S1. confirm 归属行打印位于 try 块之外，打印异常时 thinking._paused 残留（低风险，与 ask/decide 卡结构细节不一致）

- **维度**：逻辑正确性（健壮性）
- **代码位置**：src/glaucous/cli.py:1034-1039——pause（L1034）与归属行打印（L1035-1038）均在 try（L1039）之前，finally resume（L1065-1066）只覆盖 try 块。对照 ask 卡（cli.py:270-271，pause 后即入 try）与 decide 卡（cli.py:324-325，同序）：呈现整体位于 try 保护内。
- **缺陷说明**：若归属行的 console.print 抛异常（rich 打印失败属极小概率），异常在 finally 保护范围之外向上传播，thinking._paused 残留 True——后续动态区事件降级直打（呈现降级、不崩溃），轮末 close()（cli.py:834 置 _paused=False）与下轮 start_turn()（cli.py:811）会重置，状态可恢复。
- **修复方向**：归属行打印移入 try 首行（pause 已执行，时序与可见性不变），与 ask/decide 卡结构完全对齐。

### S2. child_note 去重行为无测试断言，本轮失效未被 205 passed 暴露

- **维度**：Spec 符合性（§8.1 事件通道用例对 r2-S2 承诺口径的覆盖缺口）
- **代码位置**：tests/test_subagent.py:402-419（test_sub_events_recorded_in_session_events）——仅断言 session_events 落账（kinds == ["sub_start", "sub_event", "sub_end"]），无去重计数断言。
- **说明**：B1 的失效因此完全无测试信号（WSL 全量 205 passed 与去重失效并存）。
- **修复方向**：B1 修复后补用例：同 agent 连续 text 增量 ×N → thinking.add/直打恰 1 次、session_events 落账 N 条；防回归。
## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| r2-B1 复核 | confirm 归属行时序：cli.py:1034 pause() → L1035-1038 归属行，先 pause 后呈现，与 ask 卡（L270 pause → L272+ 呈现）/ decide 卡（L324 pause → L331+ 呈现）同序；r2 实证该形态（对照组）在 ThinkingView 自管擦除协议下「归属行打在动态区原位、持续可见」；两条决策路径（箭头 L1042 / 数字回退 L1051）均在其后 | ✓ |
| r2-B1 波及面 | 主 agent 路径行为不变：active_agent == "主 agent" 时归属行不打印；pause 先行无输出副作用（ThinkingView.pause 仅置位 + 擦除，无块时空操作 cli.py:745-754/820-823），与修复前可见行为等价；confirm 状态切换逻辑（ctx.active_state or ctx.state，L1054-1056）未动；SubmitPlanTool(confirm=confirm) 接线（L1068）不变 | ✓ |
| r2-S1 复核 | 三处 escape(f'[{agent_id}]')：render_event sub_event text 分支（cli.py:607-609）、sub_event else 分支（L630-631）、make_on_event 降级直打（L1119-1121）；WSL 实证 render_event 三分支输出均含 [child-N] 字面量（'  [child-1] 正文生成中…'、'[child-3] ◆ t（plan·每次审批）'）——/expand 重放与降级路径的子 agent 标识均可见 | ✓ |
| r2-S1 波及面 | 无残留同型点：其余 agent_id 消费处（sub_start L596、tool_start L615、sub_end L636）为 escape(agent_id) 裸标识、无方括号字面量上下文；_thinking_line 返回的 [child-N] 纯文本（L688/L690）消费方均整行 escape（thinking.add L789 / render_event L631）；ask/decide/confirm 归属行任务文本均经 escape（L278/L337/L1037） | ✓ |
| r2-S2 落账部分 | session_events 落账照常：实证 50 增量落账 50 条（A_recorded: 50），/expand 全量回看不变，符合声明口径（去重呈现部分失效见 B1） | ✓ |
| 逻辑正确性（波及面）| make_on_event 其余分支未触碰：text/diagnostic/budget/tool_start/默认路径与 r2 行为一致；sub_event text 分支（L1105）先于 budget 分支（L1123）的次序保持，子 budget 不误入 text 分支、不落 ctx.last_budget 口径不变 | ✓ |
| 一致性（范围控制）| git diff --stat 与 r2 报告同一文件集（7 修改 + 3 新增），本轮改动限于 cli.py，无范围蔓延 | ✓ |
| 逻辑正确性（运行验证）| WSL 全量 ~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q = **205 passed**（4.06s，复现声明；基线 ≥192 守恒）；import glaucous.cli 冒烟通过 | ✓ |

## 四、复审要求

- **B1**（必须）：child_note 声明上移至 make_on_event 闭包层（`def on_event` 之前），使「同 agent 连续 text 增量只在首条触发 thinking.add/降级直打」真实生效；随后 r4 聚焦复审该点（建议随附 S2 去重测试用例）。本轮其余通过项无需重开。
- **S1/S2**：可与 B1 顺带修复，或登记 TODO.md 后续批次处理。