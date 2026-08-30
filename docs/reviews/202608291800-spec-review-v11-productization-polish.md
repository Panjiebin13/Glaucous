# Spec 一致性评审报告：v1.1 前置产品化打磨（技能创建 / 补全增强 / 思考折叠 / 用量展示 / 交互升级）

> 评审日期：2026-08-29 18:10
> 评审对象：`docs/designs/202608291800-plan-v11-productization-polish.md`（v1.0 待评审）
> 对照文档：`docs/编程智能体需求文档.md`（v3.2）、`docs/编程智能体概要设计说明书.md`（v4.1）、`docs/Glaucous开发计划表.md`（v1.0）、`docs/Glaucous天青夏日主题设计.md`、`TODO.md`（评审债务登记）、代码现状（src/glaucous/*、pyproject.toml、requirements.txt）
> 结论：**不通过**（阻塞 4 项，建议 10 项）

## 一、评审范围

「上游依据」声明：需求文档（FR-26/27/28/30/31/33）、概设 §6/§7/§8/§9/§10、开发计划表、天青夏日主题设计。四份文档相对路径均实际存在；概设 §6~§10 章节号真实；FR 编号真实。链接可达性通过。

实际评审覆盖：R1~R7 与附加项 A~D 的需求覆盖与决策闭环、与概设渲染/权限/网关/工程结构约定的一致性、范围边界（与计划表 M4 债务、v1.1 排期的衔接）、§十 测试声明、以及 spec 声称的 12 处代码现状逐项事实核对（详见第六节）。附加项与 `TODO.md` 中 r3-S5 / 3.2r B-01 / r3-S2 / r3-S3 债务登记的吻合性见第七节。

## 二、阻塞问题

### B1. R6 箭头选择选项集与既有三选项契约不一致（疑似静默削减「同意同类型」与方案三选一）
- **维度**：需求一致性
- **spec 位置**：§6.2 「`make_decision_callback`：审批卡选项（同意/拒绝）改箭头选择；选中「拒绝」后进入既有拒绝理由文本输入（含 B 项保护）；取消（Esc）视为拒绝且理由为「用户取消」」；「`prompt_plan_decision`：方案卡（执行/拒绝）同改箭头选择，取消视为拒绝、理由「用户取消」」
- **上游位置**：需求文档 §1.2 FR-11「每次审批三选项：同意 / 同意同类型（本会话同类操作不再询问）/ 拒绝（可附理由）」（P0）；FR-08「呈现三选一：①开始构建，每次请求权限；②开始构建，同意所有权限；③继续讨论一下」（P0）；概设 §5.3「三选项：[a] 同意 / [b] 同意同类型 / [c] 拒绝（可附理由）」；概设 §5.2「用户面对一次性三选一」；代码现状 `cli.py` make_decision_callback 非 DANGEROUS 为 a/b/c 三选项、prompt_plan_decision 为 1/2/3 三选一并支持「3 反馈内容」
- **冲突说明**：审批卡箭头选项文字上只剩「同意/拒绝」两项，「同意同类型」（FR-11，P0，`test_approval_flow` 有豁免语义用例）去向未声明；方案卡「执行/拒绝」与既有三选一（含「同意所有权限」与「继续讨论」）完全不对应，且 PlanDecision 无「理由」字段（只有 feedback）。若按字面实现即构成对 P0 需求与概设 §5.2/§5.3 接口契约的静默削减；若仅为行文简写则 spec 未 decision-complete。
- **修复方向**：存疑，提请作者确认。逐卡显式枚举箭头选项全集与选中后的映射：审批卡（DANGEROUS 与非 DANGEROUS 分别列出，明确「同意同类型」是否保留）、方案卡（①②③与箭头项的一一映射及 Esc 的落点，明确「理由」对应 PlanDecision.feedback 的写法）；如确有削减意图，须按裁剪原则显式声明并登记。

### B2. R6 选择器在运行中的 asyncio 事件循环内以同步方式运行，spec 未给可行接线方案，且异常回退会掩盖失效
- **维度**：结构与可执行性
- **spec 位置**：§6.1 「prompt_toolkit `Application`（`full_screen=False`）……任何构造/运行异常 → 返回 None 由调用方走数字回退」；§6.3 验收「pty 环境：提问卡/审批卡/方案卡均可 ↑↓ 选择、Enter 确认、Esc 取消」
- **上游位置（代码事实）**：三处被接线回调均为同步函数（`cli.py` make_ask_callback/make_decision_callback/prompt_plan_decision），且均在运行中的事件循环内被调用——`agent/loop.py` `result = await self._registry.dispatch(call, mode_snapshot)`，审批/提问发生在 dispatch 链路内；现状实现用阻塞 `console.input`/`input()` 规避了事件循环问题
- **冲突说明**：prompt_toolkit 的同步 `Application.run()`（含构造期事件循环绑定）无法在已运行的 asyncio 事件循环内安全执行（需 `run_async()` 或等效桥接）；按 spec 的同步回退设计，真实运行（agent 循环内）大概率抛错并统一落入「返回 None → 数字输入」回退——三卡在 pty 下实际永远走数字输入，§6.3 验收不可达，而 §6.1 的兜底子句恰好掩盖该失效。若改为异步桥接或回调异步化，则触及 `ApprovalPipeline`/`SubmitPlanTool.confirm` 的同步回调契约，spec 均未决策。
- **修复方向**：存疑，提请作者确认。补充选择器在「同步回调 + 运行中事件循环」场景下的执行方案（如 run_async 桥接、独立线程/终端接管、或将审批/提问接线点改为异步并声明对 ApprovalPipeline 回调契约的影响），并保证异常回退不会静默吞掉「本应可用的箭头选择」。

### B3. R3 折叠触发点「最终回答开始」判定所述机制不成立（现有 stream_state 无法区分最终回答与中间步正文）
- **维度**：结构与可执行性
- **spec 位置**：§3.1 「**最终回答开始**（首个最终回答 text_delta，判定沿用现有 stream_state 逻辑）：Live 区停止并原地收缩为一行摘要」
- **上游位置（代码事实）**：`commands.py` `stream_state: dict[str, bool] = field(default_factory=lambda: {"printed": False})`——仅「本轮是否已打印流式正文」布尔；`cli.py` make_on_event 对**任何** "text" 事件置 printed=True；`agent/loop.py` 对循环内**每一次** `self._llm.chat(...)` 都传 `on_text=self._emit_text`——中间步响应若同时携带正文与 tool_calls（协议允许、现状渲染分支亦按此存在），其正文同样以 text 事件流式输出
- **冲突说明**：现有事件流中不存在「这是最终回答的首个增量」的信号——只有流结束后（无 tool_calls）loop 才能判定自然终止，而此时流式已结束。按 spec 字面把「首个 text_delta」当作折叠触发点，多步任务的中间步正文会提前触发收缩，折叠行为依赖模型是否输出中间正文，不确定且不可验收。实现正确的折叠点必然需要在 loop 侧引入新事件/缓冲判定（并可能改变流式时序），但 spec 断言「沿用现有逻辑」，且 §九 实现位置汇总 R3 行未列 `agent/loop.py`。
- **修复方向**：明确折叠触发的真实信号来源并补齐相应改动点：或声明 loop 新增事件（如「最终回答开始/自然终止预判」）并同步修订 §九 表与 §5.2/§7 依赖该时序的排版顺序；或改用不依赖预判的呈现方案并给出可验收的判定条件。

### B4. R3 Live 动态区与阻塞式交互输入的共存未设计，且缓冲范围表述与实际事件通道不符
- **维度**：结构与可执行性
- **spec 位置**：§3.1 「**TTY 且折叠开启**……中间事件（tool_start/tool_end/ask/decision/compressed/重试提示等，即除最终回答 text_delta 外的全部 on_event）不逐条直接打印，而是渲染进一个 `rich.live.Live` 动态区」
- **上游位置（代码事实）**：`agent/loop.py` 事件契约注释「事件类型：text / tool_start / tool_end / diagnostic / mode_changed」（M2/M3 后另有 budget / compressed）——**不存在** ask / decision / 重试 事件；提问卡与审批卡由 `make_ask_callback`/`make_decision_callback` 直接 `console.print` + **阻塞** `console.input` 完成（含附加项 B 要保护的拒绝理由输入）；重试提示是 `LLMClient.on_retry → ThemeRenderer.retry` 回调，同样不经 on_event
- **冲突说明**：① ask/decision/重试提示并非 on_event 事件，§3.1 的缓冲范围表述与实际通道不符，实现者须自行决定这些交互的渲染归属；② `rich.live.Live` 持续重绘期间执行阻塞 `console.input`（审批三选项、提问回答、拒绝理由、R6 的 PT 选择器）会发生输入提示被重绘覆盖/交错的典型冲突，而审批与求助恰是本轮交互的主路径，spec 未给出 Live 暂停/让位的时机设计（§3.1 仅定义了「最终回答开始」与轮末两个退出点）。
- **修复方向**：存疑，提请作者确认。将缓冲/呈现范围按真实通道拆分（on_event 事件 vs 卡片回调），并补充「交互输入发生前暂停（或收缩）Live、输入完成后恢复」的时序约定；同步核对附加项 B 的拒绝理由输入与 R6 选择器在该时序下的行为。

## 三、建议问题

### S1. R5 usage 事件的发射主体与接线缺失（§九 表漏 agent/loop.py）
- **维度**：结构与可执行性
- **位置与摘录**：§5.1 「在现有事件回调通道新增 `on_event("usage", payload)`（`LLMClient.chat`/`_chat_once` 签名追加 `on_usage`……由 cli 接线）」；§5.2 「`make_on_event` 处理 `usage` 事件」；§九 R5 行仅列 `llm/client.py、cli.py、commands.py`
- **建议**：`make_on_event` 收到的是 AgentLoop 的事件（`loop.py` `_emit`），而 chat 由 loop 发起——usage 从 client 到 cli 必须经 loop 转发（loop 传 on_usage 给 chat 并 `_emit("usage", …)`，事件契约注释同步更新）。请在 §5.1 补一句发射主体，并在 §九 R5 行补 `agent/loop.py`。

### S2. R5 「每轮」口径不一致：折叠摘要要「合计」，渲染只缓存单次
- **维度**：结构与可执行性（内部自洽）
- **位置与摘录**：§3.1 摘要行「约 Xk tokens（X = 本轮已上报 usage 的 prompt+completion 合计）」；§5.2 「缓存到 `ctx.last_usage`（`last_usage: dict | None`）」
- **建议**：多步轮次中每次 chat 都会上报 usage，`last_usage` 单字典会被后一次调用覆盖——用量行（§5.2）显示的是最后一次调用的数据，与 §3.1 的「本轮合计」口径不同，两处数字可能互相矛盾。请明确口径：整轮累加（建议同时给出累加字段与清空时机，与 turn_events 同批）或明确「仅最后一次调用」。

### S3. 「stream_end」非既有事件名；「rebuild_loop 后新 ctx 字段自然重置」表述与代码事实不符
- **维度**：结构与可执行性（术语与表述）
- **位置与摘录**：§3.1 「每轮结束（stream_end 处理完后）清空」「与既有『整体替换』重建语义兼容（rebuild_loop 后新 ctx 字段自然重置）」；§5.2 「最终回答结束（stream_end）后」；§7 「流结束后（stream_end 处理）」
- **建议**：现行事件契约无 `stream_end` 事件；实际的轮末时点是 repl 中 `await ctx.loop.run(task)` 返回后（及异常/中断路径），请统一改为明确时点表述或声明新增事件。另 `/clear`、`/resume` 是在**同一** ReplContext 对象上替换内部组件并经 rebuild_loop 重建 loop/pipeline，ctx 本身不重建、字段不会「自然重置」——显式清空要求已写明（保留），请删改该理由句避免误导。

### S4. R7 「cli 流式分支已有累积变量」与事实不符，且「实现时二选一」为决策残留
- **维度**：结构与可执行性
- **位置与摘录**：§7 「回答原文从流式累积文本取（`cli` 流式分支已有累积变量，直接复用；若无则于 stream_state 增加 `answer_parts` 累积——实现时二选一，保持单一数据源）」
- **建议**：代码事实：cli 侧无任何正文累积变量（make_on_event 只渲染不累积），但 `ctx.loop.run(task)` 的返回值即最终回答全文（loop.py 自然终止返回 `msg.text`），这是现成的单一数据源。请改为确定方案（建议直接用 run 返回值），删除「二选一」。

### S5. R7 回答卡片与概设 §8.4「正文不套面板」约定相抵触，宜显式登记偏离
- **维度**：概设一致性
- **位置与摘录**：§7 「流结束后……追加渲染完整回答为 Markdown 卡片：`make_card("🕊 回答")` + `Markdown(text)`」；概设 §8.4「正文流式 Markdown 内联渲染，不套面板」
- **建议**：头部决策记录已载「md 渲染为流式结束后追加卡片」（用户裁定），不构成阻塞；但正文未声明与概设 §8.4 的偏离，且「流式原文 + 卡片」双份呈现将成为常态。请在 §七 加一句偏离声明（指向用户决策），并登记概设 §8.4 相应条款的后续修订；另建议在验收清单注明双份呈现为预期形态。

### S6. `stream_options` 对不支持的 OpenAI 兼容网关无降级
- **维度**：结构与可执行性（可行性风险）
- **位置与摘录**：§5.1 「`_chat_once` 流式请求 kwargs 增加 `stream_options={"include_usage": True}`」；§5.3 「供应商不返回 usage → 整轮无 usage 行，其余流程逐字节不变」
- **建议**：§5.3 只覆盖「收到响应但无 usage」；若目标网关不识别 `stream_options` 返回 400，按 `client._is_retryable` 属不可重试错误，整轮直接失败（超出「展示层」影响面）。主目标供应商（DeepSeek）支持该参数，故仅建议：补一条降级（如 400 后去掉该参数重试一次，或以环境变量门控），与概设「降级路径」风格保持一致。

### S7. FR-31 常驻状态栏（bottom_toolbar）裁剪未指向偿还任务号
- **维度**：需求一致性（裁剪登记）
- **位置与摘录**：§0 范围裁剪「/theme 暗亮切换、FR-31 常驻状态栏（bottom_toolbar）」；`TODO.md` Day5「3.3 ……FR-31 常驻状态栏可用 PT `bottom_toolbar` 承载」仍为未勾选待办
- **建议**：该裁剪已显式声明（不构成静默遗漏），但偿还去向未指明任务号/里程碑（M4 或 v1.1）。TODO 已有登记，建议在 §0 该条补一句「维持 TODO Day5 3.3 待办，随 M4 偿还」以闭合偿还路径。

### S8. 附加项 A 的数据源口径与债务登记（r3-S5）及头部行既有口径不一致
- **维度**：结构与可执行性（与既有评审记录衔接）
- **位置与摘录**：§八 A「repl 启动时传入 `config.profile.model` 与 `state.mode`」；`TODO.md` r3-S5「Banner 第三行接 `ctx.current_model` 与模式段」；代码现状 `cli.py` `render_prompt_header(ctx.current_model, report)` 用档案名
- **建议**：`config.profile.model` 是模型名字段（如 deepseek-v4-flash），`ctx.current_model` 是档案段名（如 env/段名），两者取值不同；r3-S5 登记的是 `ctx.current_model`，头部行现状亦用 `ctx.current_model`。Banner 仅启动时渲染一次，两口径都「可讲」，但请统一为一个数据源（建议与头部行一致用 `ctx.current_model`），避免同一屏两处口径分叉。

### S9. R1 SKILL.md 未提示写文件的 Build 模式前提；§十 未交代 R1/R7 的测试归属
- **维度**：结构与可执行性
- **位置与摘录**：§1.2 第 3 点「目标路径……用现有写文件工具即可，不得写工作区之外」；概设 §5.1「Plan 模式下，写类工具根本不出现在发给 API 的 tools 定义里」；§十 测试清单未含 R1（新资产可解析性）与 R7（render_answer_card）
- **建议**：Plan 模式声明层无写工具，模型按 create-skill 指引写文件会先被「当前为 Plan 模式」回喂——建议在 §1.2 要点中加一条「引导用户确认进入 Build 模式后再创建」的文案要求，减少一次无效尝试；R1 资产可被三层扫描解析（frontmatter 合法）与 R7 渲染建议补最小断言，或显式注明由 M4 债务 `test_skill_lazyload` / `test_theme_render` 覆盖。

### S10. COMMAND_META 单一数据源的导入方向未交代（cli↔commands 现有反向引用为函数内延迟导入）
- **维度**：结构与可执行性
- **位置与摘录**：§2.2 「抽为 `cli.COMMAND_META: dict[str, str]` 单一数据源，HELP_LINES 由它拼装」；代码现状 `cli.py` 顶层 `from .commands import …`，`commands.py` 仅在函数内延迟 `from .cli import …`（注释「反向引用只能函数内完成（避免模块环）」）
- **建议**：HELP_LINES 是 `commands.py` 模块级常量，若由 `cli.COMMAND_META` 拼装将形成模块级循环导入。请明确数据源落点（如 COMMAND_META 放 commands.py，cli 补全器引用它）或给出避免环的拼装方式。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 硬约束（§4/§5）：无框架依赖、本机执行、凭据零存储——R4 模板仅 `api_key_env` 指向环境变量名且注释明示零存储，registry 现有 `api_key` 明文拒绝校验保留；截止 9/2 前范围可行 | ✓ |
| 需求一致性 | 需求覆盖：用户 8 条原始需求 → R1~R7 + 附加项 A~D 全映射（第 8 条「其他优化」转化为 A~D，见第七节） | ✓ |
| 需求一致性 | 决策闭环：五个用户决策（仅项目级 skill / /expand / 流式后追加卡片 / 模板首次启动生成 / 缓存字段有则显示）均与正文一致（§1.2.3、§3.1、§7、§4.2、§5.2） | ✓ |
| 需求一致性 | R1 工作区约束：目标路径固定 `<工作区>/.glaucous/skills/<name>/`，复用现有写工具与沙箱，不引入新代码路径（FR-13/概设 §7.3） | ✓ |
| 需求一致性 | 裁剪声明集中（§0）且 M4 测试债务与计划表/TODO 一致（4 项测试债务留 M4，未越界） | ✓ |
| 概设一致性 | R2/R3 降级路径：管道/非 TTY 无补全器、逐条实时打印维持现状，符合既有「降级不拒启动」约定 | ✓ |
| 概设一致性 | R4：模板失败静默回退 env 兜底、已存在绝不覆盖、默认档案取首段与 `GLAUCOUS_DEFAULT_MODEL` 语义兼容（与 `load_registry` 现状一致），符合风险预案「环境变量单模型兜底」 | ✓ |
| 概设一致性 | R5 签名兼容：`on_usage` 为可选追加参数，不改 `on_text` 语义；`LLMClient` 纯传输职责不变 | ✓ |
| 概设一致性 | R6 取消语义对既有审批测试无破坏：`test_approval_flow` 等经注入决策回调验证管线，不走 CLI 交互层；§6.3 明确管道数字输入路径以既有测试模式覆盖 | ✓ |
| 概设一致性 | §九 实现位置均落于概设 §10 工程结构（cli/commands/theme/llm/extensions/assets），assets 资产路径与既有 package-data 惯例一致 | ✓ |
| 结构与可执行性 | 头部要素：日期、上游依据（含具体 FR/章节）、前置状态、决策记录齐备；四个上游文档引用全部可达 | ✓ |
| 结构与可执行性 | §十 测试声明覆盖 R2~R6 可测面并声明「本轮包含测试」；基线「67 passed 1 skipped」与 TODO 3.3i2「68 用例全绿（65 基线=64 passed 1 skipped + 压缩意象 3）」吻合 | ✓ |
| 结构与可执行性 | 斜杠命令面三处一致约束（SLASH_COMMANDS / HELP_LINES / handle_command）显式声明；R2 顺带偿还 r3-S1（/help 与补全未收录 /view） | ✓ |
| 结构与可执行性 | 附加项 C 与代码事实吻合：`/exit`、`/quit` 由 repl 内联拦截（cli.py 先于 handle_command），`_cmd_exit` 确为死代码；HELP_LINES 条目保留 | ✓ |
| 结构与可执行性 | 附加项 D 事实核对：pyproject/requirements 现状均为 `rich>=13.7` 无上限（r3-S3 属实），恢复 `>=13.7,<14` 为登记修复 | ✓ |

## 五、复审要求

不通过。必须修复并提请作者确认的阻塞项：**B1、B2、B3、B4**。

- B1/B2 集中于 §六（R6）：补齐三张卡的选项全集与映射、异步执行桥接方案；
- B3/B4 集中于 §三（R3）：重判折叠触发信号与事件通道归属，补 Live 与交互输入的共存时序；同步修订 §九 实现位置汇总（R3/R5 涉及 `agent/loop.py`）。
- 建议项 S1~S10 修复后登记 `TODO.md` 即可，不阻断复审通过判定。
- 复审按全量流程执行（R3/R6 为交互主路径改动，波及 §五/§七/§十 的时序与测试声明，需一并复查）。

## 六、附：代码现状事实核对（spec 声称 vs 实际）

| # | spec 声称 | 核对结果 |
|---|----------|---------|
| 1 | `cli.SLASH_COMMANDS` 14 个命令，无 /view、/expand（R2 需增补） | 属实（cli.py L102-105） |
| 2 | `make_prompt_session` 现用 `WordCompleter(SLASH_COMMANDS)` | 属实（cli.py L647） |
| 3 | `make_ask_callback` EOF/中断 → None（「未响应」语义）；`make_decision_callback` 现为文本键入 a/b/c，拒绝理由输入无保护；`prompt_plan_decision` 三选一、中断 → 继续讨论 | 属实（cli.py L179-279；拒绝理由保护缺口即 3.2r B-01） |
| 4 | `render_banner` 现无参三行卡片 | 属实（cli.py L79-88） |
| 5 | `stream_state` 仅 `{"printed": False}` | 属实（commands.py L86）——B3 的依据 |
| 6 | `make_on_event` 现处理 text/budget/tool_end，无 usage | 属实（cli.py L526-538） |
| 7 | `ReplContext` 无 turn_events/last_usage 字段 | 属实（commands.py L60-86） |
| 8 | `handle_command` 含 /exit//quit 分支与 `_cmd_exit`，但被 repl 内联拦截（死代码） | 属实（cli.py L737-739 先于分派；commands.py L386-387） |
| 9 | `load_registry` 文件缺失 → env 单档案兜底；`PING_TIMEOUT=15` | 属实（registry.py L59-60、L29） |
| 10 | `_chat_once` 流式 kwargs 现无 `stream_options`；`chat(messages, tools, on_text)` 签名 | 属实（client.py L129-138、L90-95） |
| 11 | `theme.py` 有 `make_card`/`render_markdown_doc`；`PT_STYLE` 为 try/except ImportError 兜底且含 glaucous.title/text/sub 类名 | 属实（theme.py L88-101、L104-140） |
| 12 | 技能三层扫描（内置→全局→项目），扫描时机为会话启动与 /clear | 属实（skills.py L73-106；cli.py L674-675、commands.py L157）——§1.2.6「/clear 后生效」表述正确 |
| 13 | pyproject package-data 现为 `assets/skills/*/SKILL.md`（单层目录无需改）；`rich>=13.7` 无上限 | 属实（pyproject.toml L37、L18；requirements.txt L10） |

## 七、附：附加项与既有评审债务登记对账（TODO.md）

| 附加项 | 声称偿还 | TODO 登记原文（摘要） | 对账结果 |
|---|---|---|---|
| A | r3-S5 | 「Banner 无模型名/模式占位：Banner 第三行接 ctx.current_model 与模式段，M4 视觉验收处理」 | 方向吻合；数据源口径有差异 → S8 |
| B | 3.2r B-01 | 「审批『拒绝理由』EOF/Ctrl+C 保护（B-01）」 | 吻合（仅覆盖理由输入阶段，与现状缺口一致） |
| C | r3-S2 | 「/exit 双路径死代码与告别文案分叉：清理 commands._cmd_exit 死分支或恢复 🌅 文案」 | 吻合（二选一取「清理死分支」） |
| D | r3-S3 | 「rich 依赖上限丢失：恢复 rich>=13.7,<14 或修订 spec §4.7 并登记」 | 吻合（取恢复上限，事实核对见第六节 #13） |
| —（未列附加项，顺带覆盖） | r3-S1 | 「/help 与 PT 补全未收录 /view」 | 由 R2 §2.1 增补覆盖 |

其余未偿还项（r1-S2/S4/S5/S6、r2-S1/S2、r3-S4 并入的 S-01/S-02/B-03、FR-31 状态栏）均仍登记于 TODO 并留 M4，spec 未声称偿还，不构成静默遗漏（FR-31 偿还指向模糊 → S7）。
