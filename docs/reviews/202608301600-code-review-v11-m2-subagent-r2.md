# 代码评审报告：V1.1-M2 多 Agent 基础（第 2 轮·聚焦复审）

> 评审日期：2026-08-30 17:40
> 评审对象：spec docs/designs/202608301500-plan-v11-m2-subagent.md；聚焦改动 cli.py（confirm 闭包 / make_on_event / render_event / _thinking_line）、agent/subagent.py（build_report / _agent_seq）、tests/test_subagent.py、TODO.md
> 模式：聚焦复审（r1 报告 B1/B2/B3 修复 + S2 顺带关闭 + 建议项登记；波及面：SubmitPlanTool 调用链、_cmd_expand 重放、ThinkingView 擦除重绘协议、测试隔离）
> 结论：**不通过**（阻塞 1 项，建议 2 项）

## 一、阻塞问题

### B1. confirm 归属行先于 live_hooks「pause」打印——默认折叠路径下被擦除协议吞没，卡面归属不可见并残留思考区首行

- **维度**：Spec 符合性 / 逻辑正确性
- **代码位置**：src/glaucous/cli.py:1031-1035——归属行打印（L1031-1034）位于 pause（L1035）之前：

      if ctx.active_agent != "主 agent":
          console.print(f"[glaucous.sub]  🕊 子 agent（任务：{escape(ctx.active_task[:40])}）[/]")
      ctx.live_hooks["pause"]()

  对照：提问卡（cli.py:270 先 pause 后打印）、审批卡（cli.py:324 同序）均为「先 pause 后呈现」。
- **spec 位置**：§4.2 原文——「方案确认卡 confirm 闭包与提问卡 make_ask_callback(ctx) 的 ask()：同样读取 ctx.active_agent，非「主 agent」时卡头增同款归属行（概设 §8.3：子 agent 的 ask_user 直接透传给用户，但**卡面可见归属**）。」
- **冲突/缺陷说明**（WSL 实证，rich 13.9.4）：ThinkingView 为自管 ANSI 擦除重绘协议（cli.py:711-724），_erase 从当前光标上移 _last_block 行后 \x1b[J 清屏到底（L743-752）。confirm 由子 agent 内 SubmitPlanTool 触发时思考区动态区处于绘制态（_drawn=True、_last_block=K≥1）。按 confirm 现次序模拟（先打行后 pause）：

      K(_last_block)=2
      归属行写入 = 'ATTRIBUTION_LINE\n'
      pause 擦除写入 = '\x1b[2A\x1b[J'

  光标推演：归属行占 block_top+K 行；pause 上移 K 行至 block_top+1 后清屏到底——**归属行落入擦除区被吞**，且思考区首行（块第 0 行）**残留在卡面上方**（重绘协议破坏）。对照组（ask/decide 次序，先 pause 后打）实测：pause 擦净后光标回到块首，归属行打在动态区原位、持续可见。
  即：折叠思考区激活（TTY 默认启用，cli.py:1468-1470 ThinkingView() if _collapse_enabled() else None）的主交互路径下，归属行对用户不可见，仅降级/管道路径（thinking=None 或未激活）可见——spec「卡面可见归属」未在默认路径达成，且新引入残留行瑕疵。
- **修复方向**：归属行打印移到 ctx.live_hooks["pause"]() 之后（与 ask/decide 同序），两条决策路径（箭头/数字回退）照常在其后；或并入 select_with_arrows 问题区与 prompt_plan_decision 卡首行。一行级调整，随后 r3 聚焦复审该点。

## 二、建议问题

### S1. [child-N] 前缀被 rich markup 解析为未知样式标签静默吞掉（本轮新增两处直打；r1 既有 render_event L629 同型）

- **维度**：逻辑正确性（呈现保真）
- **代码位置**：src/glaucous/cli.py:606-608（render_event sub_event text 分支）与 cli.py:1109-1111（make_on_event 降级直打）——f"[glaucous.dim]  [{escape(agent_id)}] 正文生成中…[/]"；同型既有代码 cli.py:629（sub_event else 分支）。
- **spec 位置**：§5.2（text 折叠摘要形态；[child-N] 前缀为本轮修复自定形态）
- **缺陷说明**：escape() 只转义内容中的方括号，agent_id（child-N）不含方括号时原样返回，[child-1] 以裸标签进入 markup 解析——glaucous.dim 是主题样式可解析，child-1 不是，被 rich 按 default=none 静默丢弃。WSL 实证（rich 13.9.4，非 TTY 与 _force_terminal 两种环境输出一致）：render_event 与降级路径捕获输出均为 '   正文生成中…\n'——子 agent 标识丢失、残留 3 空格；/expand 重放走 render_event 同样丢标识。thinking.add 路径经 escape(line) 整行转义不受影响。
- **修复方向**：写成 \[{escape(agent_id)}\] 或对拼装后的完整行 escape 后再套样式标签；顺带统一 L629 同型问题。

### S2. 子 text 增量逐条计入思考区 N 并刷同文行，与 ThinkingView 明文 N 口径冲突

- **维度**：Spec 符合性（既有口径一致性）/ 逻辑正确性（呈现质量）
- **代码位置**：src/glaucous/cli.py:1106-1107——折叠激活时每个子 text 增量调用 thinking.add（count += 1 并追加一行）；摘要形态 cli.py:685-686。
- **spec 位置**：§5.2「仅折叠摘要……直打或进动态区」（M2 spec 未定义 N 口径）；对照 ThinkingView.add_text docstring（cli.py:792-797）明文口径——「不计数（N 口径 = 非 text 事件 + 交互伪事件 + 正文段落账条目，**不含增量**，§4.3）」。
- **缺陷说明**：子正文增量本质是 text 增量，现按增量逐条 +1 步并逐条入滚动行。WSL 实证：50 个 text 增量 → N=50、滚动区 50 行同文 '[child-1] 正文生成中…'、session_events 落账 50（落账本身正确）。主 agent 正文增量走 add_text 不计数——同一动态区两套口径并存：N 被增量灌水失真、窗口被同文行刷屏；降级直打与 /expand 重放同样逐增量刷行。注：r1 修复方向原文「进动态区计 N」与既有 §4.3 口径存在张力，实现取了前者字面，故判为口径级建议而非实现偏差。
- **修复方向**：连续同文摘要行去重（合并一行，或仅首条记行、后续增量只落账不进窗口），或改走 add_text 式不计数滚动通道；session_events 逐增量落账保留不变（/expand 可回看）。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性（B2 复核）| §3.3/§六/§8.1 硬上限：build_report 预留尾注空间（subagent.py:89-91，report[:400-8] 拼尾注）；WSL 实证边界：2000 字输入 len=400 且以尾注收尾、拼装 361 字摘要恰 400 不截断、362 字截断后恰 400——任何路径 ≤400 | ✓ |
| Spec 符合性（B2 测试）| tests/test_subagent.py:303-306 test_hard_limit_400 断言收紧为 len(report) <= 400 且 endswith 尾注（r1 以越界值为界的问题消除）| ✓ |
| Spec 符合性（B3 复核）| §5.2 三形态齐备：make_on_event sub_event text 分支 = 落账（cli.py:1105，实证 50/50）+ 折叠 thinking.add（L1106-1107）+ 降级直打 dim（L1109-1111）；render_event text 分支（/expand 经 commands.py:490 重放）dim 摘要行（L604-609）；_thinking_line 内层 text 返回 [child-N] 正文生成中…（L683-686，repr 实证）。不流式直出、不触碰 ctx.text_segment/stream_state（防交叉不变量保持）| ✓（呈现瑕疵见 S1/S2）|
| Spec 符合性（S2 关闭）| §3.1 步骤 2「进程生命周期内唯一」：计数器升为类级 SubagentRunner._agent_seq（subagent.py:98-99），run 内经类名自增（L141-142）；全仓检索无 self._counter 残留（base.py 的 _consecutive_parse_failures 为无关既有字段）；空 task 在自增前 return 不消耗序号；/clear、/resume 重建 runner 后序号跨实例延续 | ✓ |
| 逻辑正确性（测试隔离）| tests/test_subagent.py 全部动态取 agent_id：L228 result.metadata["sub_agent"]、L263-268、L319-320 startswith、L363-364 events[0] payload；无对 runner 产出 id 的硬编码断言（L415-417 的 child-1 为测试自构造事件载荷，与计数器无关）；用例顺序变化不致脆断 | ✓ |
| 逻辑正确性（波及面）| confirm 签名与 SubmitPlanTool 调用链不变；make_on_event 分支次序保持——子 budget 走通用路径不误入 text 分支、不落 ctx.last_budget；render_event/_thinking_line 新分支不影响既有事件形态；build_report 改动后 sub_end brief=报告首行[:80]（subagent.py:207-209）仍取摘要段 | ✓ |
| 一致性（范围控制）| git diff --stat 与 r1 同一文件集（7 修改 + 3 新增）；本轮仅新增 TODO.md 登记（V1.1-M2 建议项节：S1/S3/S4/S5/S6/S7 六条并标注 S2 顺带关闭，TODO.md:140-147），无范围蔓延 | ✓ |
| 逻辑正确性（运行验证）| WSL 全量 python -m pytest tests/ -q = **205 passed**（3.45s，复现声明）；import glaucous.cli 冒烟通过 | ✓ |

## 四、复审要求

- **B1**（必须）：confirm 归属行移至 ctx.live_hooks「pause」() 之后（与 ask/decide 同序），随后 r3 聚焦复审该点；本轮其余通过项无需重开。
- **S1/S2**：可与 B1 顺带修复，或登记 TODO.md 后续批次处理；S1 涉及 r1 既有 L629 同型点，建议一并统一口径。
