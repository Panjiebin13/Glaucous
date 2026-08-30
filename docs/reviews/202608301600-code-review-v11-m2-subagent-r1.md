# 代码评审报告：V1.1-M2 多 Agent 基础（第 1 轮）

> 评审日期：2026-08-30 16:00
> 评审对象：spec docs/designs/202608301500-plan-v11-m2-subagent.md；代码全量（相对 f88480c：6 个修改文件 + 3 个新增文件，git status 与声明范围逐一核对一致）
> 模式：全量评审（改动清单：新增 src/glaucous/tools/spawn_agent.py、src/glaucous/agent/subagent.py、tests/test_subagent.py；修改 src/glaucous/context/history.py、src/glaucous/permission/approval.py、src/glaucous/ui/prompts.py、src/glaucous/tools/base.py、src/glaucous/commands.py、src/glaucous/cli.py）
> 结论：**不通过**（阻塞 3 项，建议 7 项）

## 一、阻塞问题

### B1. spec §4.2 要求的「方案确认卡」子 agent 归属标注缺失（三卡只做了两卡）
- **维度**：Spec 符合性
- **代码位置**：src/glaucous/cli.py:1009~1050（confirm 闭包全文无任何 ctx.active_agent 读取）与 cli.py:397~426（prompt_plan_decision 的方案卡「:clipboard: 方案已就绪」无归属行）；对照已实现的两处：cli.py:273~279（提问卡）、cli.py:333~338（审批卡）。SubmitPlanTool 经 self._confirm(plan)（tools/planning.py:130）进入该闭包，是方案卡唯一展示路径。
- **spec 位置**：§4.2 原文——「方案确认卡 confirm 闭包与提问卡 make_ask_callback(ctx) 的 ask()：同样读取 ctx.active_agent，非「主 agent」时卡头增同款归属行（概设 §8.3：子 agent 的 ask_user 直接透传给用户，但卡面可见归属）。」；决策记录 3 亦声明「两类交互卡的归属标注，经 ReplContext.active_state / active_agent 切换实现隔离」。
- **冲突/缺陷说明**：检索 cli.py 全文，active_agent 仅出现在 ask 卡与 decision 卡两处；confirm 闭包（含箭头路径 select_with_arrows 与降级路径 prompt_plan_decision）均无归属呈现。子 agent 内 submit_plan 兜底触发时（spec 决策 3：工具保留仅作兜底），用户看到的方案卡与主 agent 无异，无法辨识归属——spec 明文要求的行为未实现。
- **修复方向**：confirm 闭包读 ctx.active_agent，非「主 agent」时向卡头注入归属行——箭头路径可在 select_with_arrows 问题行前直打一行「[glaucous.sub]🕊 子 agent（任务：…）[/]」（或在 confirm 内打印归属行后再出选择器）；降级路径给 prompt_plan_decision 增加可选归属参数渲染进方案卡首行。保持与 ask/decision 卡同一格式（ctx.active_task[:40] + escape）。

### B2. 结构化报告截断路径突破 400 字硬上限（实测 408 字），测试以越界值为界固化
- **维度**：Spec 符合性 / 逻辑正确性（不变量破坏）
- **代码位置**：src/glaucous/agent/subagent.py:89~90——if len(report) > REPORT_MAX_CHARS: report = report[:REPORT_MAX_CHARS] + REPORT_TRUNCATED_SUFFIX（先截到 400 再拼 8 字尾注 → 408）；测试 tests/test_subagent.py:301~304 断言 len(report) <= 400 + len(「…（报告已截断）」)。
- **spec 位置**：§3.3 原文——「报告总长硬上限 **400 字**（len() 计），**任何路径产出都不得超限**。」；§六 数据模型表「报告四段……总长 ≤400 字」；§8.1「报告回传（FR-63）：四段标题齐全；**总长 ≤400**」；概设 v1.1 §4.4「报告规范（工具结果文本，**≤400 字**）」。
- **冲突/缺陷说明**：WSL 环境实测 build_report(2000 字输入, []) 返回长度 **408**（尾部为尾注 8 字）。spec 内部虽有「超 400 字截断加尾注」与「硬上限 400 不得超限」的表述张力，但后者以强调句式出现于三处（§3.3/§六/§8.1）且概设同口径，「截断加尾注」的合规实现应预留尾注空间。现实现与新测试共同固化了 408 的越界行为。
- **修复方向**：subagent.py 截断改为 report[:REPORT_MAX_CHARS - len(REPORT_TRUNCATED_SUFFIX)] + REPORT_TRUNCATED_SUFFIX，tests/test_subagent.py:303 断言收紧为 len(report) <= 400；同时提请 spec 作者在后续修订中明确「截断加尾注」以硬上限为约束（消除两条款张力）。

### B3. 子 agent text 增量呈现行为未实现：既未折叠摘要也未直打，/expand 落账重放为空白
- **维度**：Spec 符合性
- **代码位置**：src/glaucous/cli.py:1088~1092（make_on_event：sub_event 且内层 event == text → 仅 session_events.append 后 return，先于 thinking.add）；cli.py:604~605（render_event sub_event 分支：inner == text 时直接 return）——两处共同导致子正文增量在折叠模式（不进动态区、不计 N）、降级/管道模式（不直打）均不可见；连带 _cmd_expand（commands.py:469~490）重放 sub_event 时经 render_event 同一 return，已落账的 text 增量回看为空白。
- **spec 位置**：§5.2 原文——「sub_event：子事件委托既有渲染形态并加两格缩进前缀（tool_start/tool_end/text 等复用现分支的紧凑形态；**text 增量在子 agent 内不做流式直出，仅折叠摘要**，防与父正文缓冲交叉——**实现取 _thinking_line 的单行形态直打或进动态区**）。」
- **冲突/缺陷说明**：spec 给出的两种实现形态（_thinking_line 单行直打 / 进思考区动态区）均未采用，选择了第三种（完全吞没）：make_on_event 在 thinking.add 之前提前 return，render_event 在委托 _thinking_line 紧凑形态之前提前 return。结果是子 agent 的中间正文对用户完全不可见（仅报告承担终态信息），与 spec 明文的「仅折叠摘要……直打或进动态区」不符；且 session_events 已为 text 增量落账却无法经 /expand 呈现，形成「记录了但看不到」的死数据。另注：_thinking_line(text, …) 现会裸返回事件名字符串 text，修复时需一并处理内层 text 的摘要形态。
- **修复方向**：①_thinking_line 增加内层 text 的单行摘要形态（如取增量尾部 ≤N 字或「💬 子正文 …」摘要）；②make_on_event 的 sub_event(text) 分支改为：落账 + 折叠激活时 thinking.add（进动态区计 N）、降级时经 render_event 直打单行摘要；③render_event sub_event 的 text 分支改为输出 dim 单行摘要而非 return。防交叉约束（不直出流式、不动 ctx.text_segment）保持不变。

## 二、建议问题

### S1. record_denial 审计路径缺归属字段（agent/agent_task），与「agent 恒有」口径存在落差
- **维度**：Spec 符合性（审计面）
- **代码位置**：src/glaucous/permission/approval.py:164~178——record_denial 的事件字典无 agent/agent_task；对照 _event（L188~201）已附 agent 恒有 + agent_task 非空附。
- **spec 位置**：§六「审计事件新字段 agent（**恒有**，取值 main / child-<N>，概设 §8.3）」；概设 v1.1 §8.3「audit.log **每条记录**增加 agent 字段」。§4.1 实现契约仅点名 _event()，属 spec 内部粒度差。
- **冲突/缺陷说明**：Plan 模式写/bash 拦截（base.py:238~248 调 record_denial）产生的审计行（含子 agent 在 Plan 快照下的拦截）无归属标注，按 §六/概设「恒有/每条」口径为缺漏；主流程 gate 审计（_event）合规。
- **修复方向**：record_denial 补 agent: self._agent_label 与条件性 agent_task；审计字段集为超集扩展，回归风险同 §8.2 预判（无严格全等断言）。

### S2. agent_id 自增计数器随 rebuild_loop 重置，/clear、/resume 后同进程内 child-1 复发
- **维度**：逻辑正确性（标识唯一性）
- **代码位置**：src/glaucous/agent/subagent.py:126（self._counter = 0 实例属性）+ cli.py:992~1007（rebuild_loop → build_registry 每次新建 SubagentRunner）。
- **spec 位置**：§3.1 步骤 2「child-<序号>（runner 内自增计数，**进程生命周期内唯一**；审计字段值与概设 §8.3 对齐）」。
- **冲突/缺陷说明**：/clear、/resume 整体重建 runner 后计数归零，同一进程（同一 audit.log）内第二个会话可再产 child-1，审计归属在该场景下有歧义。单会话主路径（含 spawn 多次派发递增）不受影响。spec「runner 内自增」与「进程生命周期内唯一」两句在 rebuild 场景下互斥，实现取了前句。
- **修复方向**：计数器提升为类属性或模块级（SubagentRunner._counter），或将 agent_id 生成改为类级自增；现有测试逐例独立环境，不受影响。

### S3. SubAgentInfo 定义后全仓无消费点（死数据类）
- **维度**：逻辑正确性（冗余代码）
- **代码位置**：src/glaucous/agent/subagent.py:48~54（dataclass 定义）；全仓检索 SubAgentInfo 仅此一处。
- **spec 位置**：§3.1/§六定义 SubAgentInfo {agent_id, task, session_file}（spec 未指明消费方）。
- **冲突/缺陷说明**：spec 要求的数据模型已落地且字段一致，但 runner 未构造该实例（run() 内 agent_id/task/session_file 以散装变量流转），当前属零引用死代码。不计阻塞（spec 未规定消费点），登记为冗余。
- **修复方向**：二选一——在 run() 构造 SubAgentInfo 并挂入 ToolResult.metadata 或事件载荷，使数据模型有真实消费；或注释注明「M5 评审子 agent 预留」。

### S4. 报告「修改文件清单」连接符与相对化处理同 spec 有措辞级偏差
- **维度**：Spec 符合性（文案级）
- **代码位置**：src/glaucous/agent/subagent.py:82——「、」.join(modified_files)；path 采集自 call.arguments（L161~171）原样入清单。
- **spec 位置**：§3.3「修改文件清单 = 收集清单（**相对路径逗号连接**）」。
- **冲突/缺陷说明**：①连接符用顿号「、」非逗号（语义等价的枚举分隔，属 M1-r1-S1 同类措辞偏差）；②路径未做相对工作区归一，模型传绝对路径时报告将携带绝对路径（通常模型传相对路径，实际影响小）。
- **修复方向**：与 spec 作者统一口径（改逗号或随下批 spec 修订为实现版）；可选：对 workspace 内路径做 relative_to 降级处理。

### S5. 提问卡归属行使单列卡隐式扩为两列，布局异常
- **维度**：逻辑正确性（渲染细节）
- **代码位置**：src/glaucous/cli.py:273~279——ask 卡为 make_card(「:dove: 想请教你」) 单列表（theme.py:104~125 非 key_value 分支仅加一列），归属行 add_row 传入两格。
- **spec 位置**：§4.2「卡头增同款归属行」（格式合规；列布局为实现细节）。
- **冲突/缺陷说明**：rich Table.add_row 对超列数行自动扩列（WSL 实测：单列卡混入两格行后列数变为 2，不崩溃），但提问卡将整体变两列布局、其余行第二格全空，视觉出现空列。审批卡（key_value 两列）无此问题。
- **修复方向**：ask 卡归属行并入标题/首行单格（如「归属　🕊 子 agent（任务：…）」），或该卡临时以 key_value 形态构建。

### S6. 子 loop 异常路径无 sub_end 配对，UI 留下无完成行的「出发」
- **维度**：逻辑正确性（健壮性）
- **代码位置**：src/glaucous/agent/subagent.py:190~206——sub_start 在 try 外发射，sub_end 仅正常返回路径发射；子 loop 异常经 finally（恢复哨兵）上抛 → 父 dispatch 收口回喂错误，但事件流中 sub_start 无配对 sub_end。
- **spec 位置**：§5.1「sub_end …时机：runner.run 出口」（异常出口未规定；本条为健壮性建议非违规）。
- **冲突/缺陷说明**：异常轮 UI 呈现为「🕊 子 agent 出发」后直接出现父侧「✘ 工具 spawn_agent 执行异常」，缺完成行，/expand 时间线不闭合。
- **修复方向**：try/except BaseException 补发 sub_end {ok: False, brief: 异常摘要 ≤80} 后再 raise，保证 start/end 配对。

### S7. spec §8.1 个别断言缺失或以等价形态降级
- **维度**：Spec 符合性（测试完备性）
- **代码位置**：tests/test_subagent.py 全文对照 §8.1 清单：①「spawn 前后父上下文 token 估算增量 = 报告文本」无显式断言（由 test_parent_history_grows_exactly_two 的父史长度恰增 2 + 报告内容断言结构性覆盖）；②防嵌套声明层断言用 all_tools()（L193）而非 spec 点名的 tool_schemas()（因 tool_schemas 由注册表派生且 spawn_agent 为 ALL_MODES，语义等价）；③「auto-approve 下普通写静默放行」无直接用例，仅由 test_metadata_and_session_file（默认 auto-approve + callback=None 下写成功且 modified_files 非空）隐式覆盖。
- **spec 位置**：§8.1「隔离性……spawn 前后父上下文 token 估算增量 = 报告文本（而非子全过程）」「build_sub_registry 产物 tool_schemas() 不含 spawn_agent」「auto-approve 下普通写静默放行」三条原文。
- **冲突/缺陷说明**：不变量实质均有覆盖，但 spec 点名的断言形态未逐条落地；登记为测试完备性建议（区别于 M1-r1-B1 的零覆盖情形）。
- **修复方向**：补一条显式断言（spawn 前后 estimate_messages(history.view()) 增量与报告长度同阶）；防嵌套补 tool_schemas(MODE_BUILD) 不含 spawn_agent 一行；auto-approve 场景补「决策回调零调用」的显式断言。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | §二 任务 2.1 SpawnAgentTool：name/description（含四段枚举超集）/parameters{task 必填, context 可选}/modes=ALL_MODES/risk=SAFE/init(runner)/execute 委托（spawn_agent.py:25~52）；仅主 registry 注册（cli.py:1007），子 registry 派生排除（subagent.py:57~68） | ✓ |
| Spec 符合性 | §三 任务 2.2 SubagentRunner.run 十一步流程逐项：空 task 兜底 ok=False（L131~136）、agent_id 自增（L138~139）、SessionState 快照复制 mode/policy/approved_types（L141~145）、History.create subdir=agents（L147 + history.py:174~205，默认 sessions 向后兼容）、子 pipeline（agent_label/agent_task，L148~154）、build_sub_registry 新实例共享工具对象排除 spawn_agent（L57~68）、归属切换 try/finally 恢复 None/主 agent/空串哨兵（L190~202，D8 不捕获实例）、子 loop 复用父参零改 loop.py（L178~188 + git 证据 loop.py 未修改）、任务注入格式 task + [补充上下文] 段（L197）、tool_end 采集 write/edit 保序去重（L161~171）、ToolResult metadata 三键（L207~215） | ✓ |
| Spec 符合性 | §3.2 prompts.py：SUB_AGENT_PROMPT 角色段（禁 ask_user/禁 submit_plan + 报告四段硬约束，L75~91）→ 工作区（L124）→ 规则全量不裁剪（L126）→ 任务描述（L127，对齐概设 §8.2 字段列举）→ 补充上下文（L129），不注入记忆/技能索引；BASE_PROMPT 多 Agent 段（L65~69） | ✓ |
| Spec 符合性 | §四 任务 2.3：ApprovalAction origin=main/origin_task=空（approval.py:40~41）；Pipeline agent_label=main/agent_task=空（L86~87，既有构造零影响）；gate 回调前 stamp、auto-approve/type_approved/无回调路径不 stamp（L103~134）；_event agent 恒有 + agent_task 非空附（L198~201）；ReplContext 三字段默认值（commands.py:136~141）；confirm 读 ctx.active_state or ctx.state 且 enter_build 作用于 active state（cli.py:1038~1040）；继承语义成立（子内 approve_type 写子副本 approval.py:148；DANGEROUS/区外守卫 gate 零改动 L97~100/L141~147；快照隔离经 test_per_action_card_and_no_backflow / test_submit_plan_approval_flips_child_copy_only 固化） | ✓ |
| Spec 符合性 | §五 任务 2.4：sub_start/sub_event/sub_end 事件契约与 payload（subagent.py:190/174~176/206，brief=报告首段 ≤80 L205）；make_on_event sub_* 落账（cli.py:1088~1097）；父子预算隔离（子 budget 事件经 sub_event 包装，不落 ctx.last_budget——cli.py:1093 分支仅命中主 budget；复用父 LLMClient 的 usage 计入父轮 turn_usage，决策 2 统计面合并） | ✓ |
| Spec 符合性 | §5.2 渲染：sub_start「🕊 子 agent 出发 · agent_id + task≤60」海盐青（cli.py:592~598）、sub_end「⎿ 子 agent … 完成 · brief」ok 分色（L626~632）、sub_event tool_start/tool_end 缩进与 child-N 前缀紧凑形态（L599~625）、_thinking_line 三分支（L674~681）——text 分支缺失见 B3 | ✓（除 B3） |
| Spec 符合性 | §七 错误处理策略 8 行逐条成立：空 task（runner 兜底）；子 loop 非 ParseCircuitBroken 异常善后悬空 call 后上抛（loop.py:127~133）→ dispatch 统一收口回喂（base.py:255~260）；ParseCircuitBroken 子内转终止诊断（loop.py:122~126）；步数上限/预算耗尽返回诊断文本照常拼报告 ok=True（loop.py:81~91）；幻觉调用 spawn_agent 回喂「不存在。可用工具：…」（base.py:192~197，测试固化）；无回调拒绝安全侧（approval.py:123~128）；落盘尽力而为（history.py:112~118）；runner 异常经 finally 恢复哨兵（subagent.py:199~202） | ✓ |
| Spec 符合性 | §8.1 新增用例 13 个覆盖主体（隔离父史恰增 2/防嵌套双保险/origin+origin_task/豁免不回流/子副本翻转父不变/哨兵恢复/审计归属字段/四段+metadata/子会话文件 session_meta/事件序列/make_on_event 落账/串行 order 断言/空 task 无会话文件）；§8.2 既有测试零修订（git 无既有测试改动）；§8.3 基线 **205 passed = 192 + 13** 守恒（WSL 实测复跑 pytest tests/ -q：205 passed in 4.46s） | ✓（缺项见 S7） |
| Spec 符合性 | 范围控制：git status 改动文件清单与 spec §九 实现位置汇总完全一致（6 修改 + 3 新增），agent/loop.py 零改动，base.py 仅 +4（all_tools）、commands.py 仅 +6（三字段），无 spec 之外功能；三项显式裁剪（并行/树、子会话回看命令面、子内 spawn 声明+执行双保险）未越界 | ✓ |
| 逻辑正确性 | 运行验证：pytest 205 passed；import glaucous.cli 冒烟通过；管道冒烟（临时工作区 /exit）EXIT=0、Banner/提示符/命令分派正常 | ✓ |
| 逻辑正确性 | 循环导入：subagent→commands 仅 TYPE_CHECKING（subagent.py:37~38）；spawn_agent→subagent 仅 TYPE_CHECKING（spawn_agent.py:21~22）；commands→cli 四处函数内延迟导入（commands.py:242/266/430/463）；cli→subagent/commands 顶层单向——全链无环 | ✓ |
| 逻辑正确性 | 事件与状态面核对：子事件不触碰 ctx.text_segment/stream_state/last_budget（make_on_event 分支次序 cli.py:1066~1101）；父 spawn 自身 tool_start 正常 flush 父正文段（§4.2 触发点 1 不受影响）；子交互卡伪事件与 live_hooks 复用主路径；父史零污染由 dispatch 既有流程保证（测试固化） | ✓ |
| 逻辑正确性 | 异常与资源：子 loop BaseException 悬空 call 善后→runner finally 恢复哨兵→父 dispatch 收口（父会话不中断）；KeyboardInterrupt/CancelledError 由子 loop 善后上抛、repl 顶层兜底（cli.py:1538~1545）；JSONL/审计写入均尽力而为；/clear、/resume 重建 runner 无旧实例滞留 | ✓（补强建议 S6） |
| 逻辑正确性 | D8 一致性：confirm 闭包动态回退 ctx.active_state or ctx.state（不捕获实例）；runner finally 恢复 None 哨兵（r2-B4 修复在位）；ProbeTool 用例验证 active_state 指向子副本且父 mode 不变 | ✓ |
| 一致性 | 风格：中文注释 + spec/概设章节引用与既有代码同构（spawn_agent.py 模块头、subagent.py 生命周期注释、approval.py:30~34/76~79、cli.py:274/333/593/1036 等逐处标注 FR/概设/spec 条目）；概设 §10/§11 安全与结构约定满足（.glaucous/ 整体 gitignore 覆盖 agents/ 子目录；新模块位于 agent/ 与 tools/ 包，符合工程结构） | ✓ |

## 四、复审要求

- **B1**（必须）：补齐方案确认卡的子 agent 归属标注（confirm 闭包读取 ctx.active_agent，箭头/降级两路径呈现），随后做 r2 聚焦复审。
- **B2**（必须）：build_report 截断预留尾注空间使任何路径 ≤400 字，同步收紧 test_subagent.py 的长度断言；提请 spec 作者确认「截断加尾注」与「硬上限 400」的张力以硬上限为准。
- **B3**（必须）：按 §5.2 落实子 text 增量的折叠摘要呈现（_thinking_line 单行形态，直打或进动态区，二选一即可），并使 /expand 重放可见。
- S1~S7 为建议项，可与 B1~B3 顺带处理，不作为放行前置条件；其中 S2（agent_id 跨会话唯一）建议随本批一并处理，成本一行。
