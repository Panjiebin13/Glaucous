# Spec 一致性评审报告：V1.1-M2 多 Agent 基础 Spec（spawn_agent + 子 AgentLoop + 权限继承 + 报告回传）

> 评审日期：2026-08-30 15:00
> 评审对象：`docs/designs/202608301500-plan-v11-m2-subagent.md`（首轮全量评审）
> 对照文档：编程智能体需求文档v1.1（§2.5 FR-60~64、§4 优先级与裁剪、§5 约束合规）、编程智能体概要设计说明书v1.1（§2.1 模块图、§4.4 上下文纪律、§7.3 双评审循环、§8 多 Agent 模块、§9/§10/§11）、Glaucous开发计划表v1.1（V1.1-M2 任务 2.1~2.5 与验收标准、M6 评测口径）
> 代码基线：`src/glaucous/`（agent/loop.py、agent/state.py、permission/approval.py、permission/modes.py、tools/base.py、tools/planning.py、tools/interactive.py、context/history.py、ui/prompts.py、llm/client.py、cli.py）与 `tests/`（14 个测试文件抽查，重点 test_approval_flow.py / test_mode_default_build.py）
> 结论：**不通过**（阻塞 2 项，建议 5 项）

## 一、评审范围

Spec 头部声明上游依据：需求文档v1.1「§2.4（FR-60~64）、§4」、概设v1.1「§3（模块图：多 Agent 模块）、§4.4、§7.3」、开发计划表v1.1「V1.1-M2 任务 2.1~2.5 与验收标准」。三份相对链接经解析全部实际可达（文件均存在）。两处章节号错位见 S1（FR-60~64 实际位于需求文档 §2.5；概设多 Agent 模块位于 §2.1 分层架构图与 §8 专章）。

实际评审范围 = 声明范围 + 文档实际触及内容：spawn_agent 工具契约、SubagentRunner 子 AgentLoop 构造与报告拼装、权限归属标注（ApprovalAction/ApprovalPipeline 扩展）、sub_* 事件通道与渲染、错误处理表、测试计划、实现位置汇总。三项决策记录（共享 SessionState / 复用父 LLMClient / 同回调卡片标注）为重点核对对象。代码核对面覆盖 spec 引用的全部接口与行为断言：AgentLoop 构造签名与熔断善后、dispatch 统一收口、validate_arguments、ApprovalPipeline gate/_event、SessionState/approved_types 生命周期、submit_plan 状态切换闭包、History.create、LLMClient.on_usage、build_registry/rebuild_loop/make_decision_callback/render_event/_thinking_line、session_events 落账路径、build_system_prompt 注入段。

备注（不计问题）：前置基线「192 passed」为作者声明，docs/reviews 内最新可查留痕为 168 passed（202608310030-code-review-v11-m1-mode-base-r2），量级吻合且无法证伪，沿用前批先例（v11-feedback-fixes 评审 S4 口径）不立案；「≥192 passed 守恒」作为验收口径本身可执行。

## 二、阻塞问题

### B1. 决策记录 1「共享 SessionState」与概设「独立实例」机制冲突，且「语义等价」论据存在两条可复现反例
- **维度**：概设一致性（核心机制冲突 + 偏离声明不实）+ 需求一致性（FR-62 权限语义）
- **spec 位置**：决策记录 1「子 agent 与父 agent **共享同一 `SessionState` 实例**：mode/policy/approved_types 天然实时继承（FR-62「复制父授权策略」以共享实现，语义等价且无复制时点问题）；串行执行下无并发写冲突」；§4.3「子 agent 内『同意同类型』落入共享 `approved_types`（构建期作用域语义不变，串行下无泄漏问题）」；§3.1 步骤 4「注册父 registry 的**全部工具实例**……排除 name == "spawn_agent"」（即 submit_plan 必然在子工具集，FR-64 亦如此要求）
- **上游位置**：
  - 概设v1.1 §8.2：「SessionState = **独立实例**，授权策略 = **复制父当前值**」；§8.3：「子 agent 共用 ApprovalPipeline 构造逻辑但**持独立 SessionState**」；计划表v1.1 任务 2.3：「权限继承：**复制父授权策略**」（需求文档 FR-62 原文为「继承父 agent 当前授权策略」，spec 决策记录将其引作「复制」系计划表用语）。
  - 代码事实：`cli.py` confirm 闭包 L943~944 `if decision.choice == CHOICE_APPROVE and ctx.state.mode == MODE_PLAN: ctx.state.enter_build()`（planning.py L6~8 明文「状态切换收敛在 CLI 闭包」）；`approval.py` L91~100 同类型豁免查 `self._state.is_type_approved(...)`；`modes.py` L12~13 approved_types 至 enter_plan/enter_build 才清空。
- **冲突说明**：偏离本身已显式声明（非静默），但「语义等价」论证不成立，存在两条共享实例独有、概设独立实例方案不会发生的状态改写路径：
  1. **approved_types 子→父泄漏**：per-action 策略下，用户在「标注为子 agent」的审批卡上选择「同意同类型」，`add_approved_type` 写入的是共享集合——子任务结束后**父 agent 本轮及后续轮（直至 mode 重入清空）的同类型操作静默放行**。spec §4.3 自认「落入共享 approved_types」却判「无泄漏问题」，与概设独立实例的隔离语义相反。
  2. **子 agent submit_plan 批准反向翻转父 mode**：Plan 模式父 agent 派发只读评审子任务（spec §2.1 明确支持）→ 子 agent 调用 submit_plan（全模式可用、按 FR-64 必须在子工具集）→ 用户批准 → confirm 闭包对**共享 state** 执行 `enter_build()` 并清空 approved_types → 父 loop 恢复后下一轮快照即 Build，写工具声明层生效；该切换反馈经子 loop 统一出口以 sub_event 形态发射，父会话的模式横幅不出现。概设独立实例下此副作用被天然隔离在子会话内。
  3. 「无复制时点问题」论据亦不成立：串行执行下子任务运行期间用户无法输入（spec §8.1 串行性自证），spawn 时刻复制即实时值，不存在陈旧窗口。
  - 另：归属标注方案（§4.2/决策记录 3）仅覆盖 ApprovalPipeline 决策卡；submit_plan 卡（ConfirmCallback）与 ask_user 卡（AskCallback）不经 gate，**无任何子 agent 归属标注**，用户在上述路径 2 中无从得知卡片来自子 agent——「独立弹审批卡（标注子 agent 身份）」（FR-62）在该路径缺口。
- **修复方向**：优先回归概设方案（方向 A）：SubagentRunner 构造独立 SessionState，spawn 时刻复制父 mode/policy/approved_types 三值，审批卡标注与审计归属机制不变（与 B2 无耦合、与 FR-64 不冲突）。若作者坚持共享方案（方向 B），须在 spec 补全上述两条改写路径的封堵设计（如子 pipeline 对 approve_type 不写共享集合、submit_plan/ask_user 卡的归属标注与状态隔离口径）并论证残余风险——注意不得以「从子工具集删除 submit_plan」封堵，那将违反 FR-64「工具集 = 父工具集去掉 spawn_agent」的明文定义。无论何者，submit_plan/ask_user 卡的归属标注口径需显式定义。（定级说明：概设两处明文「独立实例」+ 可复现反例 + 涉权限语义，判阻塞；若作者有补充论证请于复审申辩。）

### B2. 子 agent system prompt 静默遗漏概设 §8.2 明确要求的 glaucous.md 规则注入
- **维度**：概设一致性（静默遗漏本轮范围内的设计项）
- **spec 位置**：§3.2「新增 `build_sub_agent_prompt(task: str, context: str, workspace: Path) -> str`：角色段……报告规范段（硬约束）……context 非空时追加『补充上下文』段」；§九 ui/prompts.py 行「新增 build_sub_agent_prompt；BASE_PROMPT 增多 Agent 段」——通篇无规则注入，亦无裁剪声明（§〇 裁剪表三行均与此无关）
- **上游位置**：概设v1.1 §8.2：「system prompt = 子任务角色 + 工作区信息 + **glaucous.md 规则** + 任务描述（不注入记忆/skill 索引——评审员需要的是任务上下文，不是主 agent 的全部包袱）」；代码事实：`ui/prompts.py` L67~82 `build_system_prompt(workspace, rules, memory, skills)` 中规则为独立注入段（L77「项目与全局规则（glaucous.md，必须遵守）」，FR-20「全量永不裁剪、每次会话自动生效」）
- **冲突说明**：概设对子 system prompt 的组成是四项列举，「不注入」白名单仅限记忆/skill 索引，glaucous.md 规则被明确要求注入。spec 的三段式 prompt（角色/报告规范/补充上下文）静默缺失该项：子 agent 的写操作（write_file/edit_file/bash）将不受项目规则约束——规则文件中的工程约定乃至「禁改目录」类约束面在子任务中失效，且与 M5 代码评审子 agent「是否违反 Spec 约束」的检查输入（概设 §7.3）相冲突。属「静默遗漏本轮范围内的设计项」，按检查清单判阻塞。
- **修复方向**：`build_sub_agent_prompt` 增 rules 参数（经 extensions/rules.load_rules 现读现传，复用 build_system_prompt 的规则段拼装方式），并在 §六 数据模型同步签名；若确有理由不注入（如评审员场景最小化），须在 §〇/§3.2 显式声明裁剪并给出去向——但需注意概设已明文要求，声明裁剪与概设冲突，仍需给出理由。

## 三、建议问题

### S1. 头部「上游依据」两处章节号错位，且未引用概设权威专章 §8
- **维度**：概设一致性（引用正确性）+ 结构与可执行性（头部要素准确性）
- **位置与摘录**：spec 头部「需求文档v1.1.md **§2.4**（FR-60~64）」「概设v1.1.md **§3**（模块图：多 Agent 模块）」。实际：需求文档 §2.4 =「Spec 一等公民（FR-52~59）」，FR-60~64 位于 §2.5「多 Agent 协作」；概设 §3 =「技术选型增补」，多 Agent 模块见 §2.1 分层架构图（多 Agent 模块框）与 §8「多 Agent 模块（FR-60~64）」专章——后者是本 spec 最直接的上游依据，头部及正文均未引用（正文比对系评审自行对 §8 展开）。
- **建议**：头部改为「§2.5（FR-60~64）」「§2.1/§8（多 Agent 模块）」，并将概设 §8.1~§8.3 纳入关联规格声明，保证追溯链直达权威章节。

### S2. 决策记录 2（复用父 LLMClient、用量计入父轮）与 FR-61「独立 token 预算」、概设 §8.2「独立记账」字面冲突未作对照说明
- **维度**：需求一致性（覆盖表述偏差）+ 概设一致性（偏离未登记偿还口径）
- **位置与摘录**：spec 决策记录 2「子 agent **复用父 `LLMClient` 实例**：on_usage 计入父轮 turn_usage（真实成本口径，用量行可见）；FR-61『不占用父上下文』约束的是父 History，不受影响」；需求文档 FR-61：「子 agent 拥有独立 History、独立 system prompt、**独立 token 预算**；不占用父 agent 上下文」；概设 §8.2：「budget = **独立记账**（max_steps 沿用 config，token 预算独立）」。代码核实：预算**执行面**确已独立（子 loop 自有 History，`_enforce_budget` 按各自 view() 与 context_limit 估算，loop.py L161~169），合并的仅是用量**统计显示面**（llm/client.py L163~164 on_usage 回调为实例级）。
- **建议**：决策记录 2 的辩护只回应了「不占用父上下文」，未回应 FR-61 明文的「独立 token 预算」与概设「独立记账」字样——建议补一段口径对照（「预算执行独立 per-loop；用量记账并入父轮，仅为显示口径」），并注明对计划表 M6 评测项「子 agent 隔离验证（父 token 不变）」的影响为零（该项为上下文占用口径）或显式声明显示面差异，避免后续评审/验收按字面判定偏离。

### S3. §七 错误处理表两处行为描述与代码事实不符
- **维度**：结构与可执行性（错误处理闭环表述失实）
- **位置与摘录**：
  1. spec §七第 1 行「子 loop 抛异常（**含 ParseCircuitBroken**）| AgentLoop 自身善后（悬空 call 补推）后向上抛；runner 不捕获，交 dispatch 统一收口」。代码事实：子 loop 即 AgentLoop 实例，其 run() 内部捕获 ParseCircuitBroken 并善后后**返回终止诊断文本**（loop.py L122~126），不会向上抛——该场景实际落入 §七第 2 行「终止诊断作为 answer 照常拼装报告」路径，且行为更优（父史仍恒 2 条）。「runner 不捕获、dispatch 收口」路径仅适用于 LLMError 等其他异常（base.py L253~256）。
  2. spec §七末行「task 为空串 | schema required 校验拦截（dispatch 既有参数校验路径）」。代码事实：`validate_arguments` 仅校验键存在与类型（base.py L102~105），`task=""` 可通过校验并实际派发空任务子 agent。
- **建议**：①第 1 行去掉「含 ParseCircuitBroken」或改注「子 loop 内部已捕获并转终止诊断，见第 2 行路径」；②空串行改为「工具 execute 内自校验拒绝（参照 planning.py/interactive.py 的空参兜底先例）」或如实声明「空串可通过校验、由子 agent 自行应付」。避免实现者按失实表格写出死分支或漏掉补防。

### S4. sub_* 事件「会话缓冲照常落账」缺实现靶点：make_on_event 未列入 cli.py 改动面
- **维度**：结构与可执行性（实现靶点遗漏，同 M1 评审 S5 先例）
- **位置与摘录**：spec §5.1「会话缓冲照常落账（`ctx.session_events`，/expand 可回看）」；§一 分层影响表 UI 层与 §九 cli.py 行仅列「render_event/_thinking_line 增 sub_* 分支；决策卡标注；build_sub_agent_prompt；build_registry/rebuild_loop 接线」。代码事实：`make_on_event` 中 session_events.append 仅有 diagnostic（L982）与 tool_start（L996）两处，`ThinkingView.add` 只计数渲染不落账（L713~720）——sub_start/sub_event/sub_end 走既有 fall-through 路径**不会**进入会话缓冲，/expand 回看承诺不成立，除非修改 make_on_event。
- **建议**：§一 UI 层/§九 cli.py 行补列「make_on_event：sub_* 事件落账 session_events（并确认与 thinking 动态区分支的先后序）」，§8.1 事件通道用例同步补 /expand 回看断言或删除该承诺。

### S5. 卡片文案/意象/审计字段值与概设 §8.3/§9 口径不一致且未声明
- **维度**：概设一致性（视觉与口径对齐）
- **位置与摘录**：spec §4.2 审批卡标注「🪽 子 agent {origin}（父任务：{origin_task 截断 40 字}）」vs 概设 §8.3「卡片标题标注 🕊 子 agent（任务：{父任务摘要}）」；spec §5.2 运行行「🪽 子 agent {agent_id} 开始 · {task 截断 60}」vs 概设 §9「子 agent 运行行（⏺ spawn_agent <任务摘要> ❄ + 报告 ⎿ 折叠摘要）」与意象文案「🕊 子 agent 出发」；审计字段值 spec §六为「主 agent」/「sub-<HHMMSS>」vs 概设 §8.3 示例「main / child-1」；另概设 §8.3「子 agent 的 ask_user 直接透传给用户」与 spec §3.2「不要调用 ask_user」的引导口径需注明并存关系（机制透传保留 + 提示词引导避免）。
- **建议**：语义均等价、不影响实现，但概设 §9 是 v1.0 延续的视觉单一口径来源——建议对齐 🕊/⏺/⎿ 意象与「任务：」措辞，或在 spec 决策记录中显式声明偏离（🪽 为 v1.1 子 agent 专属意象等），避免实现与冒烟验收（§8.3 引用「🪽」文案）各执一词。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 硬约束合规（需求 §5：无框架/SDK——复用自研 AgentLoop 实例化；无托管服务端工具；零新凭据面；Python 3.11+/CLI/WSL 口径不变） | ✓ |
| 需求一致性 | FR-60~64 逐条覆盖且与计划表任务 2.1~2.5 一一对应（工具契约/子 loop 构造/权限归属/报告回传/mock 单测） | ✓ |
| 需求一致性 | 裁剪显式且与需求 §4 一致：并行子 agent/agent 树 = P1 显式登记；防嵌套 = FR-64 本义；M3/M4/M5 划界与计划表一致；裁剪底线（spawn_agent 上下文隔离）未被触碰 | ✓ |
| 需求一致性 | 范围守恒无蔓延：sub_* 事件、SubAgentInfo、修改文件清单收集均为已声明 FR 行为的实现细化；测试新增面均可追溯到声明行为 | ✓ |
| 需求一致性 | M5 消费方可支撑：task/context 双参可承载概设 §7.3 评审输入形态（Spec 全文+检查清单+用户反馈）；报告四段含「验证结果」适配代码评审场景 | ✓ |
| 概设一致性 | 架构落位与概设 §2.1/§11 一致（tools/spawn_agent.py、agent/subagent.py 新增路径与分层影响表吻合）；「loop.py 零改动」经核实成立（AgentLoop 构造签名/参数完全覆盖子 loop 所需） | ✓ |
| 概设一致性 | 报告规范与概设 §4.4 一致（四段结构/≤400 字硬上限/工具结果入父史/父视角仅一次调用 + ToolResult） | ✓ |
| 概设一致性 | spawn_agent 契约与概设 §8.1 一致（task/context 参数、SAFE 风险级、仅主 agent 注册）；防嵌套「声明层不可见 + 执行层不存在」双保险与 FR-64/§8.2 一致，幻觉调用回喂文案与 base.py L193 逐字吻合 | ✓ |
| 概设一致性 | 子会话文件 `.glaucous/agents/`（History.create 增 subdir 参数）与概设 §8.2/§10 一致，且天然不进 find_latest_session 与 M3 迁移扫描面（history.py L1030~1033、cli.py L1044） | ✓ |
| 概设一致性 | 决策记录 3（「独立弹卡」= 同回调卡片内归属标注）为 FR-62 括注语义的可接受实现：串行执行下无并发卡片；auto-approve 不 stamp 无副作用；gate 守卫逻辑零改动满足「沙箱与危险分类同等生效」 | ✓ |
| 结构与可执行性 | 头部要素齐全（创建日期/状态/上游依据/决策记录/前置状态），三份相对链接全部可达（章节号错位见 S1） | ✓ |
| 结构与可执行性 | 内容完备：总体架构/分层影响分析/数据模型汇总/接口定义/错误处理策略/测试与验证齐备，无「待定/TODO」未决项 | ✓ |
| 结构与可执行性 | 代码靶点真实性：AgentLoop 构造签名与熔断善后、dispatch 五类错误收口、ApprovalPipeline gate/_event 结构、History.create/create_session_file、build_registry/rebuild_loop/make_decision_callback/render_event/_thinking_line、LLMClient.on_usage、build_system_prompt 分段注入等引用与代码一致（两处失实见表 S3/S4） | ✓ |
| 结构与可执行性 | §8.2 既有测试修订预估成立：test_approval_flow.py 为键级断言（L137~141/L147~148），无事件字段集全等断言，「预计零修订」判断正确；test_mode_default_build.py 存在，mock LLM 脚本驱动模式有先例 | ✓ |
| 结构与可执行性 | 测试计划可验证：「≥192 passed 守恒」口径可执行；§8.1 断言均可机械化（父史增量恰 2 条与 loop.py 先入史后 dispatch 流程吻合；「父上下文 token 估算增量 = 报告文本」是计划表验收「父 token 不变」的精确化，与概设 §4.4 一致） | ✓ |

## 五、复审要求

**结论：不通过**——存在阻塞项 B1、B2，必须修复后复审。

1. **B1**（必须）：处置共享 SessionState 与概设 §8.2/§8.3「独立实例」的机制冲突——回归独立实例+复制方案，或补全 approved_types 泄漏与子 agent submit_plan 批准翻转父 mode 两条路径的封堵设计；同步定义 submit_plan/ask_user 卡片的归属标注口径，并修订决策记录 1 与 §4.3 的「语义等价/无泄漏」表述。
2. **B2**（必须）：为 build_sub_agent_prompt 补 glaucous.md 规则注入（或显式声明裁剪并给出与概设 §8.2 冲突的理由），同步 §六 数据模型与 §九 实现位置。
3. 建议项 S1~S5 不阻塞放行，建议随 B1/B2 修复一并处理（S1 引用修正与 S4 make_on_event 补列成本低、直接影响复审核对）；如暂缓，请登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r2），不覆盖本报告。
