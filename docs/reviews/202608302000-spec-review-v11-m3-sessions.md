# Spec 一致性评审报告：V1.1-M3 会话管理 Spec（用户级存储 / 索引 / 命令面 / 自动迁移 / 分叉 / 统计）

> 评审日期：2026-08-30 21:00
> 评审对象：`docs/designs/202608302000-plan-v11-m3-sessions.md`（首轮全量评审）
> 对照文档：编程智能体需求文档v1.1（§2.3 FR-44~51、§4 优先级与裁剪、§5 约束合规、§6 验收场景）、编程智能体概要设计说明书v1.1（§3 技术选型、§6 会话管理子系统、§8.2/§10/§11）、Glaucous开发计划表v1.1（V1.1-M3 任务 3.1~3.6 与验收标准、风险预案裁剪顺序）
> 代码基线：`src/glaucous/`（context/history.py、cli.py、commands.py、permission/approval.py、agent/subagent.py）与 `tests/`（重点 test_turn_collapse.py、test_subagent.py、test_autoprivilege_guard.py、test_protected_scope.py）
> 结论：**不通过**（阻塞 3 项，建议 8 项）

## 一、评审范围

Spec 头部声明上游依据：需求文档v1.1「§2.3（FR-44~51）、§4（fork 语义收窄确认）」、概设v1.1「§6（会话管理子系统）」、开发计划表v1.1「V1.1-M3 任务 3.1~3.6 与验收标准」。三份相对链接经解析全部实际可达（`docs/` 下三份 v1.1 文档均存在），引用章节号（需求 §2.3/§4、概设 §6 及其小节、计划表 M3 任务行）均真实存在且内容相符。

实际评审范围 = 声明范围 + 文档实际触及内容：六项决策记录（project-hash / name 只存索引 / token_used 口径 / fork 收窄语义 / 切换保护实现口径 / agents 排除声明）、§〇 裁剪表、§二 paths.py 与 history.py 改造、§三 索引模型与自动命名、§四 命令面五节、§五 启动与 resume 接线、§六 stats.py、§七 数据模型汇总、§八 错误处理表、§九 测试计划、§十 实现位置汇总。代码核对面覆盖 spec 引用的全部接口与行为断言：History.create/create_session_file/session_meta/load（含 load 三元组返回与悬空调用修复）、cli.repl 启动序列与轮末 finally 时序、find_latest_session/resume_history（含三处兜底创建）、SLASH_COMMANDS/ARG_COMPLETIONS/make_repl_completer、ReplContext/begin_turn 调用点（cli.py L1560 与 commands.py L258/L276 三处）、_cmd_clear/_cmd_resume、COMMAND_META/_COMMAND_USAGE/handle_command 分派、AuditLog 两种写入格式（approval.py `_event`/`record_denial` 的 `time` 键 vs commands.py `_audit` 的 `at` 键）、subagent.py 的 subdir="agents" 用法、既有测试对上述接口的耦合点。

用户指定四项重点核实结果先行登记：① audit.log 确认存在两种行格式（`time`/`decision`/`agent` vs `at`/`event`），spec 未登记、无 decision 行为的分布口径未定义 → **S1**；② begin_turn 置位 turn_active 与其三处调用点矛盾 → **B1**；③ fork 后 rebuild_loop 依赖顺序本身正确（先替换 ctx.history 再重建，D8 闭包经 ctx 间接引用成立）→ 通过项登记，细节缺口 → **S3**；④ /sessions 参数消解规则不完整（多命中态缺失）→ **S2**。

备注（不计问题）：前置基线「212 passed」为作者声明，docs/reviews 可查留痕为 M2 评审的 192 passed（202608301500），M2 新增量级吻合且无法证伪，沿用 M2 评审备注的先例口径不立案；「≥212 passed 守恒」作为验收口径本身可执行。

## 二、阻塞问题

### B1. turn_active 生命周期与 begin_turn 既有调用点矛盾——/clear、/resume 之后切换命令将被误拒
- **维度**：结构与可执行性（文档内部矛盾影响实现判断）
- **spec 位置**：决策记录 5「防御性落地为 `ctx.turn_active` 标志（**begin_turn 置位**、轮末 finally 复位）」；§4.5「`ReplContext.turn_active: bool = False`：`begin_turn()` 置 True；repl 任务轮 finally 复位 False」「防御性：当前 REPL 模型下输入处理发生在轮间，**该标志恒为 False**，为后续并发预留」
- **代码事实**：`begin_turn` 现有三个调用点——`cli.py` L1560（repl 任务轮入口，轮末 finally 可复位）；**`commands.py` L258（`_cmd_clear` 内）与 L276（`_cmd_resume` 内）**——后两处发生在轮间命令路径，其后不走 repl 任务轮 finally，无任何复位点。
- **冲突说明**：按 spec 字面实现（begin_turn 内置 True），/clear 与 /resume 执行完毕后 turn_active 恒为 True，用户随后输入的 /sessions \<id\>、/fork、/resume 将全部被「本轮任务执行中，无法切换会话」误拒，直到跑完一个完整任务轮才解除——与 spec 自称的「该标志恒为 False（轮间）」直接矛盾，也违背 FR-50「Build 中途阻止切换」的本意（阻止的是任务执行期间，而非刚清空/恢复完会话的空闲时刻）。内部矛盾影响实现判断，判阻塞。
- **修复方向**：置位点从 begin_turn 内部移出——改为由 cli.repl 在任务轮入口 begin_turn 之后显式置位（命令路径不置位）；或 begin_turn 增加置位开关参数、_cmd_clear/_cmd_resume 调用点显式复位。同步修订决策记录 5 与 §4.5，使「恒为 False」的论断重新成立。

### B2. session_usage 在 /fork 的口径自相矛盾：决策记录 3「重置」 vs §4.3「继承」
- **维度**：结构与可执行性（文档内部矛盾影响实现判断）
- **spec 位置**：决策记录 3「新增 `ReplContext.session_usage`（prompt/completion 会话累计，轮末由 turn_usage 累加；**/clear、/fork 重置**；切换会话时从索引 entry 恢复）」 vs §4.3 步骤 3「索引 upsert 新条目（……message_count 继承，**token_used 继承**）」+ 步骤 4「……**`session_usage` 继承当前值** → `rebuild_loop(ctx)`」
- **冲突说明**：同一行为（/fork 时 session_usage 取值）在决策记录与命令流程两处给出相反定义，实现者无从取舍，且直接决定索引 token_used 快照与 /stats 用量显示的正确性。判阻塞（存疑，提请作者确认取舍）。
- **修复方向**：按 /fork「另存为」语义取「继承」更自洽（新会话携带原会话全部历史与已发生消耗，重置会系统性低估），修订决策记录 3 将 /fork 移出重置清单；若作者确有重置理由，则反向修订 §4.3 步骤 3/4 并说明口径。

### B3. /clear 与 resume_history 兜底路径的会话创建未接入用户级存储——FR-44 静默遗漏，迁移后存储双轨分裂
- **维度**：需求一致性（静默遗漏本轮范围核心需求项 FR-44 的主创建路径）+ 概设一致性（§6.1 存储布局）
- **spec 位置**：§一 分层影响表 commands.py 行「ReplContext 增 session_usage/turn_active；新命令 _cmd_sessions/_cmd_rename/_cmd_fork/_cmd_stats；COMMAND_META/USAGE 扩充」（无 _cmd_clear）；§5.2 仅列「find_latest_session(workspace)：glob 路径改 project_dir(workspace)」「--resume \<id\> 前缀模糊匹配：同目录（当前项目）」「/sessions \<id\> 跨项目切换」三条；§5.1 步骤 2 仅覆盖 cli.repl 启动路径的 `History.create(system_prompt, workspace, session_dir=project_dir(workspace))`
- **上游位置**：需求文档v1.1 FR-44「会话文件**迁移至** ~/.glaucous/sessions/\<project-hash\>/，按项目目录哈希分目录」；概设v1.1 §6.1 存储布局图（用户级目录为唯一主会话位置）
- **代码事实**：`commands.py` L253 `ctx.history = History.create(ctx.system_prompt, ctx.workspace)`（_cmd_clear，默认旧路径）；`cli.py` L1190/L1198/L1205 resume_history 三处兜底 `History.create(system_prompt, workspace)`（--resume 无会话/找不到/恢复失败时，默认旧路径）。
- **冲突说明**：迁移（§二）把 `<workspace>/.glaucous/sessions/` 搬空留空后，/clear 与 /resume 兜底新建的会话又落回工作区旧目录——用户级/工作区双轨存储，违反 FR-44 布局；且已改用户级的 find_latest_session 永远找不到 /clear 会话（`/resume latest` 对其失效）。索引虽可经 §3.2 touch 的兜底 upsert 登记，但文件位置与声明的存储布局不符。属「静默遗漏本轮范围内的需求/设计项」，判阻塞。
- **附带证据**：`tests/test_turn_collapse.py` L246 以 `SimpleNamespace(create=lambda sp, ws: …)` 两参 fake 替换 commands.History——_cmd_clear 接入 session_dir 后该测试需同步修订。spec §9.3「如评审发现隐式依赖再修订」的预警成立，本评审即发现该隐式依赖；修订不减少用例数，「≥212 守恒」仍可达成，但 §9.3 应将其显式列入。
- **修复方向**：§一 commands.py 行与 §5.2 补列两处接线——_cmd_clear 改 `History.create(ctx.system_prompt, ctx.workspace, session_dir=project_dir(ctx.workspace))`；resume_history 三处兜底创建同参透传（或统一收敛为单一带 session_dir 的创建入口）；§九登记 test_turn_collapse.py L246 fake 的修订。

## 三、建议问题

### S1. audit.log 双行格式（time vs at）未登记，无 decision 字段行的分布口径未定义
- **维度**：结构与可执行性（错误处理口径缺失）
- **位置与摘录**：spec §4.4「审批决策分布（读 `<workspace>/.glaucous/audit.log`：按 `decision` 字段聚合计数，含 agent 维度小计 main/child-N）」；§六 `approval_distribution`「逐行 JSON 解析 audit.log（损坏行跳过），按 decision 字段聚合计数」。代码核实：审批管线事件键为 `time`/`decision`/`agent`（approval.py L189~200 `_event`、L167~179 `record_denial`）；命令审计事件（mode_switch/model_switch）键为 `at`/`event`、**无 decision/agent 字段**（commands.py L161~165 `_audit`）。
- **建议**：在 spec 登记 audit.log 存在两种行格式的事实；approval_distribution 明确「仅聚合含 `decision` 字段的行（审批管线事件），其余行跳过」，避免实现者按 `entry["decision"]` 直取对命令行报 KeyError 或落入 None 桶；§九 /stats 测试的预置 audit.log 补一条 mode_switch 行验证过滤行为。

### S2. /sessions 参数消解仅定义「唯一命中 / 未命中」两态，id 前缀多命中行为未定义
- **维度**：结构与可执行性（消解规则不完整、内部不闭合）
- **位置与摘录**：spec §4.1「kw 其他：先 `find_by_prefix`（id 前缀）→ **唯一命中**即切换会话……**未命中** → `search(kw)`」；§3.2 `find_by_prefix`「唯一命中返回」。同日多会话场景（如前缀 `20260830` 命中多条）既非唯一命中也非未命中，行为悬空；且 §3.2 的 workspace 参数在 §4.1 调用侧的作用域（当前项目优先还是全局）未指明（§5.2 仅说明跨项目切换走该接口）。
- **建议**：补第三态处理——多命中时列出候选提示精确化，或降级为名称搜索并提示「id 前缀多条匹配」；指明 /sessions 调用 find_by_prefix 时 workspace 传当前项目（同项目优先、再全项目），与 §3.2 的两段匹配语义闭环。

### S3. /fork 流程四处细节缺口
- **维度**：结构与可执行性（接口描述不精确 + 内部小口径矛盾）
- **位置与摘录**：
  1. §4.3 步骤 4「`ctx.history` 替换为 `History.load(新文件, ctx.system_prompt)`」——`History.load` 实际返回**三元组** `(history, meta_workspace, warnings)`（history.py L208/L248），未提解包；悬空调用修复产生的 warnings（写入新文件）是否呈现未说明；
  2. 索引 created_at 口径矛盾：步骤 3 upsert「**created_at=now**」 vs 步骤 2「meta 行 session_id 替换为新 id（**其余行原样**，created_at 保持原值）」+ §3.1「created_at：ISO 时间（**meta.created_at**）」——索引损坏 rebuild 后 fork 会话的 created_at 将跳回原会话值，/stats「活跃时长」随之失真；
  3. ctx.state（模式/授权策略）与 session_events（思考缓冲）在 /fork 后是否重置未说明——/clear、/resume 均执行 `SessionState()` + `session_events.clear()`（commands.py L254/L257、L272/L275），spec 步骤 4 称「与 /clear、/resume **同一重建路径**」易让实现者误以为语义全同。
- **建议**：步骤 4 改为解包写法并声明 warnings 呈现口径；created_at 统一口径（建议索引跟随 meta 原值，或 fork 时同步改写 meta.created_at 为 now，二选一）；显式定义 fork 后 state 与 session_events 的处置（建议：state 保持现状（另存为语义的「继续演进」），session_events 清空以隔离 /expand 回看面——均需写明而非留白）。

### S4. rebuild 丢失手动命名未登记为已知边界
- **维度**：概设一致性（偏离代价未声明）+ 需求一致性（FR-46 持久性削弱）
- **位置与摘录**：决策记录 2「会话 name **只存索引不落 meta**」；§3.2 rebuild「name=首条 user 消息前 20 字符」。两者组合的必然后果：索引损坏重建后，/rename 的手动命名静默回退为自动名——FR-46「/rename 随时覆盖」的持久性仅与索引文件同寿命，spec 的决策记录与 §八 错误处理表均未登记该代价。
- **建议**：比照决策记录 1 的「已知边界」写法显式登记（rebuild 后手动命名丢失、恢复为自动名）；或权衡改为 /rename 时同步回写 meta 首行（代价：改动「meta 不落 name」的声明）。二选一，避免验收时按 FR-46 字面判偏离。

### S5. 自动命名对 /skill 组装任务未适配
- **维度**：结构与可执行性（边界输入未覆盖）
- **位置与摘录**：spec §3.3「索引 name 为空 → `name = task.strip()[:20]`」。代码事实：/skill 驱动的首轮 task 为固定引导语拼接文本「请按照以下技能的指令执行。\n\n[技能 X]……」（commands.py L422~425 `ctx.pending_task`）——此类会话的自动名将为固定引导语前缀且含换行符，/sessions 列表卡展示异常。
- **建议**：自动命名取剥离引导语后的用户任务段（pending_task 组装时同步保留原始描述，或命名前过滤换行/控制字符并截断）；/rename 仍可覆盖，不影响主路径。

### S6. 索引 entry 缺概设 §6.1 的 status 字段，且「字段集不变」表述失实
- **维度**：概设一致性（简化未登记 + 引用表述不准确）
- **位置与摘录**：spec §3.1「索引数据模型（**概设 §6.1 字段集不变**）」，SessionEntry 字段为 id/name/workspace/created_at/updated_at/message_count/token_used——**无 status**。概设v1.1 §6.1 entry 明列 `{ "id", "name", "created_at", "updated_at", "message_count", "token_used", "status" }`。另 spec 逐条 entry 增加了概设没有的 workspace 字段（与 FR-45「id/名称/工作区/时间/消息数/token」对齐，合理），但「字段集不变」的声明与两侧事实均不符。
- **建议**：要么补 status 字段并定义取值（可为 M5 Spec 会话状态预留，默认值登记）；要么将表述改为「字段集对齐 FR-45；概设 status 字段暂不实现，偿还登记至 M5」——消除「不变」声明与实际差异的矛盾（简化未登记按 rubric 为建议级，故不判阻塞）。

### S7. 概设 §10 的 [sessions] storage 逃生门未提及、未登记裁剪
- **维度**：概设一致性（简化未登记）
- **位置与摘录**：概设v1.1 §10 config 增补「`[sessions] storage = "user"  # user | workspace（保留 v1.0 行为的逃生门）`」；spec 通篇无此配置面（§八仅有的 ~/.glaucous 不可创建降级是运行时兜底，非配置逃生门），未实现也未在 §〇 裁剪表登记。
- **建议**：在 §〇 裁剪表显式登记（「storage 逃生门不在 M3 范围，偿还去向 TODO/M6 评估」），或按概设补 config 读取与存储分支。二选一即可消除静默偏离。

### S8. 「~/.glaucous 无法创建」降级路径与 create 异常传播不符
- **维度**：结构与可执行性（错误处理表表述与代码事实不符）
- **位置与摘录**：spec §八「~/.glaucous 无法创建（极端环境）| 迁移/索引全部静默降级，会话功能退回 workspace 旧路径仍可用（create 默认值路径）」。代码事实：§5.1 启动路径显式传 `session_dir=project_dir(workspace)`，而 `project_dir` 的 `mkdir(parents=True, exist_ok=True)` 异常会沿 `create_session_file` 的 mkdir（history.py L183~184，无捕获）从 `History.create` 直接抛出导致启动失败——不会自动「退回默认值路径」。
- **建议**：明确降级接线（调用侧 try/except OSError 后以 session_dir=None 重试创建），或将该行改为如实描述（mkdir 失败 → 启动报错，与 v1.0 现状一致）。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 硬约束合规（需求 §5 继承项：无框架/SDK——sessions/ 纯自研模块零新依赖；无托管服务端工具；零凭据面；Python 3.11+/CLI/WSL 口径不变；git status 经 subprocess 调用符合 §5.6「不引入 Git 库依赖」） | ✓ |
| 需求一致性 | FR-44~51 逐条覆盖且与计划表任务 3.1~3.6 一一对应（3.1 存储+迁移→§二、3.2 索引+命名+/rename→§三/§4.2、3.3 列表+保护→§4.1/§4.5、3.4 /fork→§4.3、3.5 stats→§六/§4.4、3.6 单测→§九）；M3 验收三条（场景 I / 删索引重建降级 / v1.0 会话自动出现在 /sessions）全部进入 §9.4（FR-44 的 /clear 路径遗漏见 B3） | ✓ |
| 需求一致性 | 裁剪显式且合规：/fork 历史节点分叉 = 需求 §4 显式裁剪确认（P1、无需偿还，语义收窄引用准确）；授权策略持久化挂账 M6 再评估（TODO 登记）；跨平台 hash 归一化 TODO 登记；Tab 补全简版达标；裁剪底线（需求 §4「分叉/统计可先裁」）未触碰——FR-48/49 均实现 | ✓ |
| 需求一致性 | 范围守恒无蔓延：session_usage（FR-45 token 口径在「v1.0 无会话记账」前提下的必要支撑）、turn_active（FR-50 的防御性落地）、ARG_COMPLETIONS/SLASH_COMMANDS 扩充均可追溯到声明行为，无未授权新需求 | ✓ |
| 需求一致性 | 场景 I 支撑闭环成立：/sessions 列表 → id 切换 → token_used 从索引恢复 session_usage → 轮末 touch 刷新，跨会话续接数据流完整 | ✓ |
| 概设一致性 | 存储布局与索引主体结构和概设 §6.1 一致（version/projects/\<hash\>/workspace/sessions 嵌套、侧边 JSON、~/.glaucous/sessions/\<project-hash\>/ 布局、sha1[:12] 选型同概设 §3）；status 缺失见 S6 | ✓ |
| 概设一致性 | 命令面语义与概设 §6.2 逐条吻合（/sessions 三形态、/rename 同步索引、/fork 另存为+索引登记、/stats 当前+全局两区块、切换保护与未提交修改提示文案、自动迁移「原目录留空、一次性、打日志」）；/rollback 提示标注「待 M4」处理正确 | ✓ |
| 概设一致性 | 索引写入时机为概设 §6.1 的合理超集（每轮刷新增强 updated_at 时效、服务 FR-47 更新时间列与 FR-45「列表秒回」；概设「会话结束」口径被细化而非违反）；agents/ 不迁移不索引与概设 §8.2「不进会话索引」一致 | ✓ |
| 概设一致性 | 重建降级与概设 §6.1/§13「索引只做加速、损坏即重建」一致；工程落位与概设 §11 一致（sessions/paths.py、index.py、stats.py 三文件路径吻合；测试文件名与概设建议略有差异、迁移用例并入 index 测试文件，非契约偏离） | ✓ |
| 结构与可执行性 | 头部要素齐全（创建日期/状态/上游依据/决策记录/前置状态/分支声明），三份相对链接全部可达，引用章节号真实存在 | ✓ |
| 结构与可执行性 | fork→rebuild_loop 依赖顺序正确：§4.3 步骤 4 先替换 ctx.history 再调 rebuild_loop（cli.py L1156~1157 构造 AgentLoop 时经 ctx 读新值，D8 闭包不捕获旧对象成立）；细节缺口见 S3 | ✓ |
| 结构与可执行性 | 轮末接线时序成立：session_usage 累加与索引 touch 挂 repl finally（cli.py L1580~1611，KeyboardInterrupt/异常路径均先经 finally 再 continue），「turn_ok 与异常路径都执行」可达；「幂等」措辞不准（累加非幂等，但 finally 单次执行语义下无害） | ✓ |
| 结构与可执行性 | 接口改动面真实：History.create/create_session_file 增 session_dir 默认参数兼容 M2 agents（subagent.py L170 `subdir="agents"`）与既有测试（tests 均为 SimpleNamespace 鸭子类型 fake，无 ReplContext 直接构造、无 cli.repl 端到端调用，新字段带默认值即兼容）；SLASH_COMMANDS（cli.py L107~111）/ARG_COMPLETIONS（L116）/COMMAND_META/_COMMAND_USAGE（commands.py L45~73）扩充路径与代码结构吻合 | ✓（B3 的 _cmd_clear 遗漏与 test_turn_collapse 耦合除外，见 B3 附带） |
| 结构与可执行性 | §十 实现位置汇总与正文各章一致，任务号映射（3.1~3.6）无缺漏；无「待定/TODO」未决项（登记的 TODO 均指向明确偿还去向） | ✓ |

## 五、复审要求

**结论：不通过**——存在阻塞项 B1、B2、B3，必须修复后复审。

1. **B1**（必须）：将 turn_active 置位点移出 begin_turn 的命令路径调用（改为 repl 任务轮入口显式置位，或 begin_turn 加开关参数 + 命令路径复位），修订决策记录 5 与 §4.5，保证「轮间恒为 False」成立。
2. **B2**（必须）：统一 /fork 的 session_usage/token_used 口径（建议继承，修订决策记录 3），或反向修订 §4.3 并说明理由。
3. **B3**（必须）：为 _cmd_clear 与 resume_history 三处兜底创建补 session_dir 接线（§一 commands.py 行、§5.2、§九 test_turn_collapse 修订登记同步补列）。
4. 建议项 S1~S8 不阻塞放行，建议随 B1~B3 修复一并处理：S1（audit 双格式与过滤口径）、S2（多命中消解）直接影响 §4.1/§六 可实现口径，修复成本低优先处理；S3~S8 可逐条登记去向（TODO.md 或 spec 内声明）。如暂缓，请登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r2），不覆盖本报告。
