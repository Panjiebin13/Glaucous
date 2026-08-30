# Spec 一致性评审报告：V1.1-M2 多 Agent 基础 Spec（spawn_agent + 子 AgentLoop + 权限继承 + 报告回传）—— r2 聚焦复审

> 评审日期：2026-08-30 16:30
> 模式：**聚焦复审**（改动范围：头部/决策记录 1~4、§一、§3.1、§3.2、§4.2~4.4、§5.1/5.2、§六、§七、§8.1、§九；其余章节继承 r1 结论，不做全量重评）
> 评审对象：`docs/designs/202608301500-plan-v11-m2-subagent.md`（第 2 轮；上一轮报告：`docs/reviews/202608301500-spec-review-v11-m2-subagent.md`，结论不通过，B1/B2 阻塞 + S1~S5 建议）
> 对照文档：编程智能体需求文档v1.1（§2.5 FR-60~64、§4）、编程智能体概要设计说明书v1.1（§2.1、§4.4、§7.3、§8.1~8.3、§9）、Glaucous开发计划表v1.1（V1.1-M2 任务 2.1~2.5 与验收标准）
> 代码基线（本轮实际核对面）：`permission/approval.py`、`permission/modes.py`、`agent/state.py`、`agent/loop.py`、`tools/base.py`、`tools/planning.py`、`ui/prompts.py`、`context/history.py`、`extensions/rules.py`、`commands.py`（ReplContext/_cmd_clear/_cmd_resume）、`cli.py`（make_decision_callback / confirm 闭包 / make_ask_callback / make_on_event / render_event / _thinking_line / rebuild_loop）
> 结论：**不通过**（阻塞 2 项，建议 4 项）

## 一、评审范围（聚焦复审）

按聚焦复审规则，本轮只复查 r1 报告列出的十个改动章节及其波及面；未改动且不涉波及的章节继承 r1「通过」结论。因 B1 修复触及维度二核心机制（SessionState 实例归属、审批管线、交互卡归属通道），波及面核查覆盖全文对「共享/独立」表述的残留引用（§2.1 注释残留见 S9）；因 B2 修复触及 FR-20 规则注入语义，已复核 §3.2 注入段与 `build_system_prompt`（prompts.py L75~77）的格式同源性。头部三份相对链接（需求文档v1.1 / 概设v1.1 / 开发计划表v1.1）经解析全部实际可达。

本轮重点核查两件事：① B1/B2 是否真正消除（对照概设 §8.2/§8.3 与 approval.py、cli.py、commands.py 实际接口）；② 修复是否引入新的不一致——尤其 active_state 切换方案与 confirm 闭包实现（cli.py L943~944 经 `ctx.state` 动态属性访问，改读 `ctx.active_state or ctx.state` 可行）、ApprovalAction.origin 与 ctx.active_agent 双通道自洽性（子任务期间两通道同取 agent_id=child-N，finally 后各回默认值，串行下无竞态——自洽；但默认值声明存在矛盾，见 B3）。

## 二、上轮问题处置核对

### 上轮阻塞项处置

| 编号 | 处置 | 说明 |
|---|---|---|
| B1 | **已修复（消除）** | 决策记录 1 改为「独立 `SessionState` 实例 + 快照复制父当前值」（L11），与概设 §8.2「SessionState = 独立实例，授权策略 = 复制父当前值」、§8.3「持独立 SessionState」、计划表任务 2.3「复制父授权策略」一致。r1 两条反例均经代码路径复核确认封堵：①同类型豁免唯一写入点在 `ApprovalPipeline.gate()` 的 `self._state.add_approved_type(...)`（approval.py L124），子管线以 child_state 构造（§3.1 步骤 5）→ 豁免只落子副本，cli.py `decide()` 回调（L315~380）不写任何 state，父侧 `approved_types` 不受染；②submit_plan 批准的状态切换收敛在 CLI confirm 闭包（planning.py L7~8「状态切换收敛在 CLI 闭包，本工具不持 SessionState」），闭包改读 `ctx.active_state or ctx.state`（§4.4）后 `enter_build()` 作用于子副本——modes.py `enter_build()` 清空的是被调用 state 自身的 approved_types（L40~50），父会话 mode 与豁免均不受影响。§4.3 快照继承语义声明与 §8.1 三条对应用例（origin/origin_task 断言、approved_types 不回流、Plan 下批准后父 mode 仍 plan + active_* 恢复）均可机械化验证。r1 附带缺口（submit_plan/ask_user 卡无归属标注）已由决策 3 + §4.2 三类卡（审批/方案/提问）统一标注补齐，符合 FR-62「标注子 agent 身份与所属父任务」与概设 §8.3 卡片标题口径 |
| B2 | **已修复（消除）** | §3.2 `build_sub_agent_prompt` 增 rules 参数与规则段（L147~150）：「rules 非空时注入 `项目与全局规则（glaucous.md，必须遵守）：\n{rules}`——全量不裁剪（FR-20 同源语义）」，注入文案与 prompts.py L77 逐字一致；工作区段与 L75「当前工作区：{workspace.resolve()}」同格式；「不注入事实记忆与技能索引」显式声明，恰好落在概设 §8.2「system prompt = 子任务角色 + 工作区信息 + glaucous.md 规则 + 任务描述（不注入记忆/skill 索引）」的四项列举内。runner 构造参 rules 及接线「rules=load_rules 传入」（§3.1 L116、§九 cli.py 行）闭环（load_rules 存在于 extensions/rules.py L45） |

### 上轮建议项落实

| 编号 | 处置 |
|---|---|
| S1 | **已落实**：头部改为「§2.5（FR-60~64）、§4（P0 范围与裁剪底线）」「§2.1（模块图：多 Agent 模块）、§4.4、§7.3、§8、§9」，逐一与上游实文核对相符（FR-60~64 确在需求 §2.5；概设 §8 为多 Agent 专章、§9 含运行行与意象文案），权威专章 §8 已纳入关联规格 |
| S2 | **已落实**：决策 2 补口径对照（L12）——「预算执行面完全独立……本条仅合并统计显示面」，与代码事实（预算 per-loop、on_usage 实例级统计）相符，满足 r1 给出的两种落点之一 |
| S3 | **已落实**：§七 两行重写与代码事实相符——ParseCircuitBroken 由子 loop 内部捕获转终止诊断不上抛（loop.py L122~126）、BaseException 善后后上抛（L127~133）；空串经 validate_arguments 仅查键存在（base.py L103~105），改为 runner 入口兜底（§3.1 步骤 1、§七首行、§8.1 空 task 用例三处一致，且校验先于 History.create，「不产生子会话文件」可满足） |
| S4 | **已落实**：make_on_event 落账分支列入 §5.1（L210）、§九 cli.py 行、§8.1 事件通道用例；落账靶点真实存在（cli.py L996 通用 append 兜底、text 早退不落账 L978、diagnostic 单独 append L982） |
| S5 | **部分落实**：决策 4 / §4.2 / §5.2 已对齐概设 §8.3/§9 口径（🕊 意象、「任务：」措辞、审计取值 main/child-N）；但 §8.3 交互冒烟文案残留旧口径（🪽 与「（父任务：…）」），见新建议 S6 |

## 三、阻塞问题

### B3. §4.1 与 §六 对同一新增接口字段声明了两个不同的默认值（「主 agent」 vs 「main」），审计归属取值随之自相矛盾
- **维度**：结构与可执行性（文档内部矛盾影响实现判断）+ 概设一致性（审计字段与概设 §8.3 对齐）
- **spec 位置**：§4.1（L174~177）「`ApprovalAction` 增字段：`origin: str = "主 agent"`、`origin_task: str = ""`（归属标注，审批卡与审计消费）」「`ApprovalPipeline.__init__` 增参：`agent_label: str = "主 agent"`、`agent_task: str = ""`（默认值 = 主 agent 行为完全不变）」「`_event()`：审计事件增 `"agent": self._agent_label`」；§六（L228~230）「`ApprovalAction` 新字段 | `origin: str = "main"`」「`ApprovalPipeline` 新参 | `agent_label: str = "main"`」「审计事件新字段 | `"agent"`（恒有，取值 `main` / `child-<N>`，概设 §8.3）」。另决策 4（L14）「agent 标识取值 `main` / `child-<序号>`（审计 agent 字段）」、§8.1（L260）「审计事件含 `agent`（`main`/`child-1` 取值）」
- **上游位置**：概设v1.1 §8.3「audit.log 每条记录增加 `agent` 字段（`main` / `child-1`）」；需求文档v1.1 FR-62「审计日志记录归属」
- **冲突说明**：同一字段在同一份 spec 中被两处规范性章节赋予不同默认值：按 §4.1 实现，主 agent 审计事件的 `agent` 值经 `_event` 直取 `agent_label` 将为「主 agent」，与 §六「恒取 main/child-<N>」、决策 4、§8.1 断言及概设 §8.3 全部冲突（`test_subagent.py` 按后者断言即失败）；按 §六 实现，§4.1 的默认值声明失实。这是本轮 §六 修正（r1-S5 的 main/child-N 对齐）未同步回 §4.1 造成的修复波及面缺口——§4.1 恰是实现 approval.py 时最直接依据的章节。附带同一波及缺口：§4.1 称 origin「审批卡与审计消费」，而 §六改称「供回调侧/测试消费」且 §4.2 审批卡实际读 `ctx.active_agent`（非 action.origin），消费方描述亦不一致。双通道本身（gate stamp 的 origin 与 ctx.active_agent）在子任务期间同取 agent_id、职责清晰，判定自洽；矛盾仅在默认值与消费方描述的声明层。
- **修复方向**：以概设 §8.3 口径为准，将 §4.1 两处默认值统一为 `"main"`（与决策 4、§六、§8.1 一致）；同步把 §4.1 的 origin 消费方描述改为「回调侧/测试消费（审计值经 agent_label 写入）」。一行级修改。

### B4. §3.1 步骤 7 的 finally 恢复值「`ctx.state`」与 §4.4 的 `active_state=None` 哨兵语义矛盾，按字面实现将使 /clear、/resume 之后 confirm 闭包读到旧 state 实例
- **维度**：结构与可执行性（内部矛盾 + 按字面实现引入真实缺陷路径）+ 概设一致性（FR-39/submit_plan 批准回 Build 的工具面出口失效风险）
- **spec 位置**：§3.1 步骤 7（L138）「finally 恢复为 `ctx.state` / `"主 agent"` / `""`」；§4.4（L194~195）「`active_state: SessionState | None = None`（None 语义 = 等于 `ctx.state`，属性访问处统一回退）」「`confirm` 闭包（cli.py）：……改读 `ctx.active_state or ctx.state`……主 agent 路径行为不变（**active_state 为 None 回退原字段**）」
- **上游位置**：代码事实 `commands.py` L93~94「/clear、/resume 会**整体替换** history/state/loop/pipeline；命令层与回调层一律经本对象间接引用（**D8：闭包不捕获旧对象**）」，L248 `ctx.state = SessionState()`（/clear）、L266 `ctx.state = state`（/resume）——两处均为整体替换且不触碰（也未声明触碰）`active_state`；`cli.py` L943~944 confirm 闭包按 §4.4 方案读 `ctx.active_state or ctx.state`
- **冲突说明**：§4.4 把 None 定义为「动态回退 ctx.state」的哨兵，并以「主 agent 路径 active_state 为 None」为主路径不变量；§3.1 步骤 7 却要求 finally 将 `active_state` 恢复为 `ctx.state` **实例**。首次 spawn 之后主路径的 active_state 永远不再是 None，§4.4 的不变量被违反，且产生可复现缺陷序列：spawn（finally 置 active_state=旧实例 A）→ /clear 或 /resume（ctx.state 整体替换为新实例 B，active_state 仍指 A）→ 主 agent 调 submit_plan → confirm 闭包 `ctx.active_state or ctx.state` 读到**旧实例 A**：A 为 build 时 Plan 下批准不再触发 `enter_build`（planning.py L5~6「PLAN→Build 唯一工具面出口」失效，FR-39 出口断裂）；A 为 plan 时 `enter_build()` 落在 A 上，当前会话 state B 纹丝不动。同时违反 commands.py 明文的 D8「闭包不捕获旧对象」不变量。`ctx.state`（实例）与 `None`（哨兵）在无 /clear、/resume 的常规路径下表现等价，但二者是 materially 不同的恢复语义，实现者按哪个写无法从文档判定；按字面（实例）实现即引入上述缺陷。存疑部分仅剩作者意图（若本意即哨兵，则属笔误级），按「宁可升级」原则定阻塞并提请作者确认。
- **修复方向**：步骤 7 finally 的 active_state 恢复值改为 `None`（哨兵，即 §4.4 语义下的「等于 ctx.state」），active_agent/active_task 恢复值不变；§七「不残留子任务值」表述同步（恢复 None 即不残留）。不建议改走「/clear、/resume 增加主动重置 active_*」路线——多一处遗漏面且仍与 §4.4「主路径为 None」的表述冲突。

## 四、建议问题

### S6. §8.3 交互冒烟文案残留旧口径「🪽 子 agent …（父任务：…）」，与决策 4/§4.2 的 🕊 新口径不一致
- **维度**：结构与可执行性（术语/文案不统一，S5 修复残留）
- **位置与摘录**：spec §8.3（L277）「per-action 下子 agent 危险操作独立弹卡且标注「🪽 子 agent …（父任务：…）」」vs 决策 4（L14）「交互卡头与运行行用 🕊 意象，格式「🕊 子 agent（任务：{父任务摘要 ≤40 字}）」」、§4.2（L181）「`[glaucous.sub]🕊 子 agent（任务：{ctx.active_task 截断 40 字}）[/]`」；上游锚点概设 §8.3「卡片标题标注 🕊 子 agent（任务：{父任务摘要}）」
- **建议**：§8.3 冒烟验收文案是人工比对的直接依据，残留旧意象与旧措辞会让冒烟执行者按 🪽/「父任务：」判定失败。同步为 🕊/「任务：」。

### S7. §3.1 SubagentRunner 构造签名未列 `ctx` 参数，与步骤 7「构造参 `ctx: ReplContext`」不同步
- **维度**：结构与可执行性（接口定义与正文不同步）
- **位置与摘录**：§3.1 签名块（L108~122）参数表为 llm/parent_registry/state/audit/decision_callback/workspace/rules/max_steps/context_limit/outputs_dir/plans_dir/on_event，无 ctx；同节步骤 7（L139）「runner 需持有 ctx 引用（**构造参 `ctx: ReplContext`，接线时传入**；active_* 三字段为新增）」
- **建议**：签名块补 `ctx: ReplContext` 一行（含注释「归属切换与 finally 恢复所需」），避免实现者按签名落地时遗漏 ctx 而在步骤 7 处返工。

### S8. §5.1 「测试固化（§七）」内部引用错位，实际用例在 §8.1
- **维度**：结构与可执行性（交叉引用失准）
- **位置与摘录**：§5.1（L211）「父上下文零污染不变量：spawn 全程父 History 仅追加 2 条……测试固化（**§七**）」；§七为错误处理策略表，无测试内容；对应隔离性断言实际位于 §8.1（L257）「断言父 History 长度增量恰为 2（assistant+tool）」
- **建议**：「（§七）」改为「（§8.1）」。

### S9. §2.1 工具契约注释残留 B1 旧方案表述「子 agent 受共享 state 模式约束」
- **维度**：结构与可执行性（B1 修复波及面残留，与决策 1 矛盾）
- **位置与摘录**：spec §2.1（L84）「`modes = ALL_MODES   # Plan 下可派发只读评审任务；子 agent 受**共享 state** 模式约束`」vs 决策 1（L11）「子 agent 持**独立 `SessionState` 实例，构造时快照复制父当前值**……绝不共享实例」、§3.1 步骤 3「快照复制，此后与父完全隔离」
- **建议**：注释改为「子 agent 受其状态副本（快照复制）的模式约束」一类表述。机制定义（§3.1/§4.3）唯一且明确，故仅判建议，但该注释是全文唯一残留的「共享」表述，位于 §3.1 之前，建议随 B3/B4 一并清理以免重植 r1-B1 的混淆。

## 五、通过项（本轮实际复查）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 需求 §4/§5 硬约束波及面复查：本轮改动不触及无框架/凭据/提交物约束面，无新增违反（继承 r1 通过结论） | ✓ |
| 需求一致性 | FR-62 修复后重查：快照复制实现「继承父当前授权策略」；独立弹卡 + 归属标注 + 审计 agent/agent_task 覆盖「审计日志记录归属」；同类型豁免写入点唯一（approval.py L124 self._state），父侧不回流 | ✓ |
| 需求一致性 | FR-61 口径对照（决策 2 补充）：预算执行面独立（per-loop context_limit 与预算管线）、父上下文零增长；显示面合并已显式声明，与概设 §8.2「独立记账」不冲突 | ✓ |
| 概设一致性 | B1 机制对齐复核：决策 1 = 概设 §8.2「独立实例，授权策略 = 复制父当前值」/§8.3「持独立 SessionState」/计划表 2.3「复制父授权策略」；SessionState 构造器支持三参快照（modes.py L33~38） | ✓ |
| 概设一致性 | B2 修复复核：§3.2 注入段 = 概设 §8.2 四项列举；规则段文案与 prompts.py L77 逐字一致；「不注入记忆/技能索引」在概设白名单内 | ✓ |
| 概设一致性 | S5 修复复核（除 S6 残留外）：决策 4/§4.2/§5.2 的 🕊/⎿ 意象、「任务：」措辞、main/child-N 审计取值与概设 §8.3/§9 对齐；ask_user「机制透传 + 提示词引导避免」并存关系已在 §3.2 显式声明（概设 §8.3 允许） | ✓ |
| 概设一致性 | S1 修复复核：头部章节号与上游实文逐一相符；三份相对链接可达 | ✓ |
| 结构与可执行性 | 双通道自洽核查：审批卡读 ctx.active_agent（§4.2，闭包经 ctx 动态访问）与 ApprovalAction.origin（gate 内 stamp，approval.py L110 callback 调用点唯一、auto-approve 等不达路径不 stamp 可行）在子任务期间同取 child-N，finally 后各回默认；串行执行下无竞态（默认值声明矛盾另列 B3） | ✓ |
| 结构与可执行性 | active_state 切换 × confirm 闭包核查：cli.py L943~944 现按 `ctx.state` 动态属性访问，改读 `ctx.active_state or ctx.state` 无需重构即可落地；切换窗口（§3.1 步骤 7）覆盖子 loop 全程，子内批准只翻子副本（modes.py enter_build 清空调用者自身豁免）；/clear、/resume 波及缺陷另列 B4 | ✓ |
| 结构与可执行性 | §3.1 run() 流程可落地：空 task 校验先于 History.create（§8.1「不产生子会话文件」成立）；set_approval_pipeline 存在（base.py L147~149）；幻觉调用回喂文案与 base.py L193 逐字一致；AgentLoop 构造签名（loop.py L50~61）与步骤 8 逐一吻合，「loop.py 零改动」复验成立（ctx 缺列见 S7） | ✓ |
| 结构与可执行性 | S3 修复复核：§七 ParseCircuitBroken/非 ParseCircuitBroken/空 task 三行与 loop.py L122~133、base.py L103~105 行为一致 | ✓ |
| 结构与可执行性 | S4 修复复核：make_on_event 落账三处登记（§5.1/§九/§8.1），落账路径真实（cli.py L996/L982/L978） | ✓ |
| 结构与可执行性 | §九 实现位置汇总与改动面一致：commands.py 状态层行已增、cli.py 行含 make_on_event 落账分支与 confirm 闭包读 active_state、runner 组装含 rules=load_rules | ✓ |
| 其余章节 | FR-60/63/64 覆盖、spawn_agent 契约、防嵌套双保险、报告四段规范、子会话文件位置、§8.2 测试修订预估、范围守恒与裁剪登记等——未改动且不涉本轮波及 | 继承上轮 ✓ |

## 六、复审要求

**结论：不通过**——B1/B2 已确认消除，但修复波及面引入两项阻塞，必须修复后作 r3 复审：

1. **B3**（必须）：§4.1 的 `origin`/`agent_label` 默认值统一为 `"main"`（对齐概设 §8.3、决策 4、§六、§8.1），并同步 origin 消费方描述。
2. **B4**（必须）：§3.1 步骤 7 finally 的 active_state 恢复值改为 `None`（哨兵语义），消除 /clear、/resume 后 confirm 闭包读旧 state 实例的缺陷路径；如作者有保留实例恢复的意图，请于复审申辩并同步修订 §4.4 与 /clear、/resume 的重置声明。
3. S6~S9 不阻塞放行，建议随 B3/B4 一并处理（S6/S9 均为旧口径残留清理，成本极低）；如暂缓，登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r3），不覆盖本报告。
