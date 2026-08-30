# 代码评审报告：V1.1-M2 多 Agent 基础（第 4 轮·聚焦复审）

> 评审日期：2026-08-30 17:47
> 评审对象：spec docs/designs/202608301500-plan-v11-m2-subagent.md；聚焦改动 src/glaucous/cli.py（make_on_event child_note 闭包层声明 / confirm 归属行入 try）、tests/test_subagent.py（新增去重回归用例）
> 模式：聚焦复审（r3 报告 B1 修复 + r3-S1/r3-S2 顺带修复；波及面：make_on_event 闭包生命周期与落账/渲染分支、confirm 闭包时序、ThinkingView pause/resume 协议、测试覆盖）
> 结论：**通过**（阻塞 0 项，建议 1 项）

## 一、阻塞问题

无。

## 二、建议问题

### S1. 去重回归用例未覆盖降级直打分支（thinking=None 路径），「直打恰 1 次」仅动态实证、未固化断言

- **维度**：Spec 符合性（r3-S2 修复方向口径「thinking.add/直打恰 1 次、session_events 落账 N 条」的直打半边）
- **代码位置**：tests/test_subagent.py:421-449——test_child_text_dedupe_counts_once 仅以 FakeThinking（active=True）覆盖折叠路径；thinking=None 的降级直打路径（cli.py:1124-1126 console.print）无用例断言
- **说明**：去重判定（first_note，cli.py:1117）为两分支共享的唯一逻辑，已被折叠路径用例固化，B1 失效形态回归必被 L445 断言暴露；直打分支本轮动态实证符合（C 组 50 条直打恰 1 行、D 组交替 2 行，见通过项），故不构成阻塞。剩余风险仅为：未来单独破坏直打分支（如 console.print 移出 if first_note）时无测试信号。
- **修复方向**：后续补一条 thinking=None + console 输出捕获的直打计数用例，或登记 TODO.md 后续批次处理。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| r3-B1 复核（静态）| child_note 声明位置：cli.py:1086 位于 make_on_event 函数体（L1074 def）内、def on_event（L1088）之前——闭包捕获、跨事件调用持久；判定式 L1117（not child_note or child_note[0] != agent）在正确作用域下即满足声明语义（同 agent 连续增量只触发一次，交替 agent 各打首条）| ✓ |
| r3-B1 复核（动态实证）| WSL glaucous 环境内联脚本（stdin 喂入，工作区零落盘），复刻 r3 失效口径：A 组折叠路径 50 条同 agent（child-1）增量 → thinking.add 恰 1 次（r3 失效时 50）、session_events 落账 50 条 ✓；B 组交替 child-2×2 → 总 add 2（新 agent 首条再计 1 次，r3 失效时 4 全打）✓；C 组降级路径 50 条 → 直打「正文生成中」恰 1 行（r3 失效时 50）、落账 50 ✓；D 组降级交替 → 直打 2 行 ✓。r3 两个症状（N 被增量灌水、滚动区同文行刷屏）均消除 | ✓ |
| r3-B1 波及面 | 落账先行再去重（cli.py:1115 append 先于 L1117 判定）：去重仅作用于呈现（thinking.add/直打），/expand 全量回看不变，符合「落账照常」声明口径；sub_event text 分支（L1110）先于 budget 分支（L1128）次序保持，子 budget 不误入 text 分支、不落 ctx.last_budget 口径不变 | ✓ |
| r3-B1 生命周期 | child_note 随 make_on_event 回调实例存续（rebuild_loop 每次新建回调，cli.py:1151）：/clear、/resume 重建后无残留，与 r3 修复方向预期一致 | ✓ |
| r3-S1 复核 | confirm 归属行移入 try 首行：cli.py:1034 pause() → L1035 try → L1036-1041 归属行（try 内首行，注释标注 r3-S1）→ L1067-1068 finally resume——归属行打印异常不再绕过 finally resume；与 ask 卡（L270-271 pause→try→呈现）、decide 卡（L324-325 同序）结构完全对齐；pause 先行、归属行仍为 try 内首个可见动作，时序与可见性不变 | ✓ |
| r3-S1 波及面 | confirm 其余逻辑未触碰：decision 初始化（L1042）、箭头/数字两决策路径（L1043-1053）、active_state or ctx.state 回退（L1056）、enter_build（L1057-1058）、flush/伪事件落账/step（L1059-1065）与 r3 报告通过项一致 | ✓ |
| r3-S2 复核 | test_child_text_dedupe_counts_once（tests/test_subagent.py:421-449）：FakeThinking 计数——同 agent 50 条增量 thinking.add 恰 1 次（L445）、session_events 落账 50 条（L446）、交替 agent child-2 再计 1 次（L448-449 总 2）；断言口径与本轮修复声明一致，r3-B1 失效形态若回归必被暴露 | ✓ |
| 一致性（范围控制）| git 面与 r3 报告同一文件集（7 修改 + 3 新增）；TODO.md diff 为 r1 建议项登记（S1~S7，M2 批次既有内容），非本轮夹带；本轮改动限于声明两点 + 一个测试用例，无范围蔓延 | ✓ |
| 逻辑正确性（运行验证）| WSL 全量 ~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q = **206 passed**（3.93s，复现声明；205 → +1 去重回归用例，基线 ≥192 守恒）；import glaucous.cli 冒烟 OK | ✓ |

## 四、复审要求

无（B1 已消除、S1/S2 已落实；S1 直打分支补测可登记后续批次，不构成本轮复审义务）
