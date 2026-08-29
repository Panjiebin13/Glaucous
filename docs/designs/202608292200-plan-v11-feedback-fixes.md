# v1.1 验收反馈修复批次（F1~F4）

> 版本：v1.1（评审修复版）
> 日期：2026-08-29
> 状态：已批准（经 2 轮评审：r1 不通过 B1~B4/S1~S7 已修复；r2 有条件通过，S8/S9 已收口，报告见 docs/reviews/ 同名 spec-review 系列）
> 上游依据：docs/designs/202608291800-plan-v11-productization-polish.md（前批 spec，R1~R7 已交付）、docs/编程智能体需求文档v1.1.md（FR-28/30/31）、docs/Glaucous开发计划表v1.1.md
> 前置状态：前批已交付并经用户 WSL 真实终端验收，验收反馈 3 组问题（附运行日志）立项本批次
> 决策记录（用户已确认）：
> ① 正文呈现——中间步正文允许临时泄露（生成中在思考动态区滚动可见），最终必须被收缩折叠；最终回答在流结束后从缓冲完整显示一次；
> ② 工具结果大卡片全部取消（不只 md 文件），工具结果一律只进思考区摘要，完整内容经 /expand 回看；最终回答的 🕊 md 卡片保留；
> ③ /skills 不再显示任何加载状态（避免误导），新增 /skill <名> [任务描述] 手动调用：立即执行、仅当次生效。

## 〇、目标与范围

修复前批验收暴露的三组问题：补全交互与 Claude Code 期望不符（默认无选中、一次 Enter 即执行、/model 无参数补全）；/skills「未加载」文案误导；思考过程折叠语义与用户期望偏差（tool_end md 卡片刷屏、中间步正文碎片泄露且无法折叠、/expand 仅回看上一轮而非全会话）。

**需求编号**：
- F1 补全交互增强：默认选中第一条、两段式 Enter 确认、/model 模型名参数补全
- F2 /skills 文案修正：去加载状态 + 用法说明行
- F3 /skill 手动调用命令：立即执行、当次生效
- F4 思考折叠语义重构：中间步正文进动态区滚动、工具卡片全取消、/expand 回看全会话

**范围裁剪（本轮不做）**：
- 管道/非 TTY 模式的折叠行为（维持现状全量输出，tool_end md 卡片在管道本就不渲染，取消卡片对管道无感）；
- FR-31 常驻状态栏（沿用前批裁剪；偿还去向：登记 TODO.md，与 FR-31 对应）；
- 思考过程跨 /stop 落盘持久化（/resume 后思考缓冲为空态；偿还去向：TODO.md 新条目，随 v1.1 会话管理增强评估）；
- /skill 的 Tab 参数补全（F1 仅覆盖 /model；登记 TODO.md 偿还）；
- M4 既有测试债务不变。

**测试声明**：本轮包含测试（§五），随代码一并交付；既有 115 passed 1 skipped 基线中与本批改动冲突的用例随语义同步修订，总数不得回退至 115 以下。

## 一、F1 补全交互增强

### 1.1 默认选中第一条

- 补全菜单弹出时**默认选中第一条候选**（现状无选中），用户 ↑↓ 可移动，直接 Enter 即确认当前选中项；
- 实现：PromptSession 的补全状态出现且无选中项时自动选中第一候选（before_render 钩子或等价机制：检测 `buffer.complete_state` 非空且 `complete_index is None` 时调 `complete_next()`），不引入第三方库；自动选中仅改变高亮、不把候选文本落入输入行（与 1.2 两段式不冲突）；
- 键入继续输入时选中态跟随前缀过滤刷新，仍保持选中第一条。

### 1.2 两段式 Enter（核心交互变更）

- **补全菜单打开时**：Enter 仅**接受当前选中候选**（补全文本落入输入行、菜单关闭），**不提交执行**；
- **补全菜单未打开时**（自由对话、或补全已被上一段 Enter 关闭）：Enter 正常提交执行；
- 适用于命令段与全部参数段（/view 路径、/model 模型名）——统一语义：第一次 Enter 选中，第二次 Enter 执行；
- 实现：自定义 `key_bindings` 覆盖 PromptSession 默认 Enter 行为：`buffer.complete_state` 存在且有候选 → `apply_completion()`（补全文本落入输入行、菜单关闭，complete_state 随之清空）；否则 `accept()`。Escape → `buffer.cancel_completion()`（complete_state 清空），随后 Enter 直接执行（跳过补全，S1 修复：状态衔接闭环）；
- 管道/纯文本降级路径无补全器，不受影响；
- 自动化边界：两段式 Enter / 默认选中第一条 / Escape 跳过补全依赖 prompt_toolkit 运行时交互（前批 §十.7 同型声明），不纳入单测，列入用户真实终端验收清单；单测覆盖补全器候选逻辑（§五）。

### 1.3 /model 参数补全

- `make_repl_completer` 签名扩展：`make_repl_completer(workspace: Path, model_names: Callable[[], list[str]] | None = None) -> Completer`（延迟取值：切换模型后列表动态跟随，不缓存快照）；
- 参数段注册表由前批 `PATH_ARG_COMMANDS = {"/view"}` 扩展为 `ARG_COMPLETIONS = {"/view": <路径补全>, "/model": <模型名前缀过滤>}`（**取代** PATH_ARG_COMMANDS，超集关系）；模型名候选来自 `model_names()`——数据源为 `llm/registry.py` 模型注册表的档案名列表（repl 构建 LLMClient 时一并传入闭包，S7 修复：非工具注册表），前缀过滤与命令段一致；
- 空格后无输入 → 列出全部模型名；前缀过滤后无匹配 → 无候选。

### 1.4 验收点

- 键入 `/` → 菜单弹出且第一条高亮；Enter 后命令文本落入输入行且**未执行**；再 Enter 才执行；
- `/view ` → 第一条文件候选选中；↑↓ 换选；Enter 选中路径，再 Enter 执行 /view；
- `/model ` → 列出全部模型名；`/model deep` 过滤到 flash/pro 中的匹配项；
- 普通对话输入 Enter 行为不变（一次提交）。

## 二、F2 /skills 文案修正

- `commands._cmd_skills`（经 `extensions/skills.py` 的索引文本）：**删除全部加载状态展示**（「· 未加载」「· 已加载」及任何加载态标记），条目仅剩 `[来源] 名称` + 一句描述（来源标记「内置/项目」保留，它描述来源而非状态）；
- 列表底部追加说明行：`技能在任务匹配时自动生效；也可用 /skill <名> [任务描述] 手动调用。`；
- 系统提示词中的技能索引注入（`index_text` 若被 system prompt 复用）同步不含加载状态——模型侧无感知变化（正文仍是惰性加载语义不变）；
- 命令面 meta 联动（S2）：/skills 的 COMMAND_META 文案改为「列出技能（任务匹配自动生效，/skill 可手动调用）」；/expand 的 meta 改为「回看本会话思考过程」。

## 三、F3 /skill 手动调用

### 3.1 命令规格

- `/skill <名> [任务描述]`：加载指定技能正文，连同任务描述组装为一轮任务**立即执行**（等同用户输入了一轮任务）；
- 无参数 → 提示用法与当前可用技能名列表；名字匹配要求与技能名**完全相等**（不做前缀/模糊匹配，S6 修复）；未匹配 → 报错并列出可用技能名；
- 任务描述可省略：省略时以技能正文本身作为任务指令（如 code-review 自含执行指令，可直接驱动）。

### 3.2 组装与执行链路

- 命令面四处登记（S2）：`cli.SLASH_COMMANDS` 增 `/skill`；`commands.COMMAND_META` 增「手动调用技能」条目；`_COMMAND_USAGE` 增 `/skill <名> [任务描述]`；`handle_command` 分派表增 `/skill` 分支；
- `commands.py`：`ReplContext` 新增 `pending_task: str | None = None`；`_cmd_skill(ctx, arg)` 解析并组装任务文本写入 `ctx.pending_task`，返回 True（handled）；
  - 任务文本组装模板（固定）：`请按照以下技能的指令执行。\n\n[技能 {name}]\n{skill正文}\n\n用户任务：{描述或“按技能指令执行”}`；
- `extensions/skills.py`：SkillRegistry 新增按名取正文的方法（如 `skill_text(name: str) -> str | None`），复用既有扫描结果，不重复读盘；
- `cli.py repl`：`handle_command` 返回后检查 `ctx.pending_task` 非空 → 消费（置 None）并**走完整任务轮流程**（`begin_turn(ctx)`（§4.3 新定义：轮级状态重置，不动会话缓冲）→ thinking start → `run(task)` → 轮末渲染，与用户输入任务轮同一入口），随后回到输入循环；
- **当次生效语义**：`pending_task` 仅驱动一次 run()，技能正文不注入 system prompt、不常驻后续上下文；后续轮次是否再加载由模型自动决定（与既有惰性加载机制一致）。

## 四、F4 思考折叠语义重构（核心）

### 4.1 语义模型（对齐用户口径）

- 会话 = 多个任务轮；一个任务轮 = 多步 LLM 响应；每步响应的正文与工具调用**全部属于思考过程**，唯一例外是**最后一步响应的正文 = 最终回答**；
- 思考过程的呈现：生成期间**允许临时泄露**（正文增量在思考动态区滚动可见），但必须保证**最终被收缩折叠**——动态区滚动窗口自动省略旧内容，轮末（或最终回答输出前）整体收缩为一行折叠摘要；
- `/expand` 的作用域为**整个会话**：重放自会话开始（或 /clear、/resume 重置点）以来全部思考过程（含中间步正文、工具调用与结果、交互伪事件），按时间序逐条渲染。

### 4.2 事件分流（修订前批 §3.1）

- **text 增量不再直接打印**：一律 append 进动态区滚动行（与非 text 事件共用 8 行滚动窗口，diagnostic 除外——见 §4.4 步骤 2 必达豁免；正文追加为滚动行尾部，视觉等同流式生成中）；同时累积进**当前段正文缓冲**（仅内存）；
- **正文段落账判定（loop 零改动，无新事件；B1 修复）**：当前段正文缓冲由 `flush_text_segment(ctx)` 动作落账为思考过程条目（标记为正文段），触发点**仅两处**——
  1. `tool_start` 事件到达时：中间步定义为带 tool_calls 的响应，其正文之后必有 tool_start 紧随；loop 自然终止分支在终答后固定发射的 budget/mode_changed **不属于**触发点，故终答不会被误落账（代码事实：agent/loop.py 终止序列先 budget 后 mode_changed）；
  2. 交互伪事件落账前（保序：submit_plan 等审批路径中伪事件先于 tool_start 落账，正文段必须先 flush 才能维持 /expand 时序）；
  空正文段不落账不计数；`run()` 返回后（finally）当前段缓冲即最终回答（loop 语义：最后一步响应正文为最终回答），**不落账**，而是输出呈现（§4.4）；
- **非 text 事件自身**：全部照常落账缓冲（budget/mode_changed/compressed/diagnostic 等），但不触发正文段落账；
- **tool_end md 卡片删除**：`_render_md_tool_end` 及其 pause/print/resume 包装整体移除；tool_end（含 read_file 打开 md）一律走思考区摘要行（现有 `_thinking_line` 口径）+ 会话缓冲；工具结果完整内容经 /expand 重放可见（tool_end 条目缓冲保留 result 全量 content）；
- **四阻塞点机制保留**：ask/decision/plan_decision/retry 同步回调仍直接打印交互卡（交互卡非思考过程），pause/resume Live 机制与伪事件落账不变。

### 4.3 缓冲口径与会话级 /expand

- `turn_events` 升级为**会话级缓冲**（重命名或保持字段名，语义变更）：条目 ① 非 text 事件 ② 交互伪事件 ③ 中间步正文段（新增，§4.2 落账）；
- **轮级状态重置 `begin_turn(ctx)`（B2 修复，取代前批 reset_turn_buffers 语义）**：清 `turn_usage`（保持前批 R5「本轮累计」口径，不跨轮累加）、轮计数器（折叠行 N）、当前正文段缓冲；**不动会话缓冲**。调用点：repl 的两处任务轮入口（用户输入任务、/skill 消费）；
- **会话缓冲重置点收窄**：仅 `/clear` 与 `/resume` 清空会话缓冲（并 begin_turn）；**下一轮任务开始不再清空**（推翻前批 r4-B2 的「轮首重置」，F4 语义变更的直接后果）；/stop 落盘不含思考缓冲（resume 后为空态）；
- `/expand`：重放全会话缓冲（分隔头改为「── 思考过程（本会话）──」）；空态提示保留；仅读不改状态；
- **折叠摘要行 N 口径**：显示**本轮**动态区收纳条目总数 = 非 text 事件（含 diagnostic，虽直打但计入）+ 交互伪事件 + 正文段落账条目（轮开始清零，与缓冲分离——缓冲会话级、计数轮级；空正文段不落账不计数，S3 修复）；异常轮步骤 5 的正文段落账发生在收缩行渲染之后，**不计入已显示的 N**（仅入缓冲供 /expand，S9 修复）；token 段沿用本轮 turn_usage。

### 4.4 轮末时序（修订前批 §3.1 步骤）

`finally` 固定顺序：
1. 动态区收缩为一行折叠摘要（`💭 思考过程（N 步 · ↑Xk ↓Yk tokens）— /expand 查看`）；
2. **diagnostic 必达豁免（B4 修复）**：diagnostic 事件**不进动态区**、始终即时直接打印（loop 契约「终止诊断必达」，若进动态区收缩后不可见会静默破坏契约），同时照常落账缓冲；_terminate 轮 `run()` 返回值为诊断文本，经 diagnostic 行呈现后，步骤 3 因正文缓冲为空自然跳过；
3. 最终回答呈现：正文缓冲非空 → 打印缓冲全文（一次性，替代原「流式逐字 + 卡片重复」），随后渲染 🕊 md 卡片（卡片保留，正文缓冲打印与卡片内容一致属预期，不视为重复缺陷——**偏离声明**：正文打印方式由逐字流式改为流末一次性输出，本条经用户确认）；
4. 用量行（R5 语义不变）；
5. 异常路径（Ctrl+C、LLMError 等中断，B3 修复）：收缩与用量行照常；**当前段正文缓冲落账为思考过程条目后清空**（保障 §4.1「/expand 重放全会话含中间步正文」对异常轮同样成立），跳过步骤 3 的正文与卡片。

### 4.5 降级与不变量

- 非 TTY / `GLAUCOUS_COLLAPSE=off`：不开动态区，text 增量维持现状逐字直接打印（管道全量语义），非 text 事件逐条打印；会话缓冲仍记录（管道 /expand 可用）；轮末仅打印用量行（无折叠行、无卡片、无正文重复输出——正文已逐字打过）；
- Live 启动失败降级：同上本轮；
- 折叠不改变事件语义：审计、预算、历史消息不受影响；
- 环境开关 `GLAUCOUS_COLLAPSE=off` 语义保留。

## 五、测试与验证

- **修订既有**：`tests/test_turn_collapse.py`——缓冲口径用例改会话级语义（轮首不清空、/clear 与 /resume 重置）、正文段落账时序（tool_start 触发落账、伪事件前保序 flush、终答后 budget/mode_changed 不触发落账、空段不落账）、异常轮正文段落账、diagnostic 直达打印、`/expand` 全会话重放与分隔头、折叠行 N 轮级口径、begin_turn 只清轮级不动会话缓冲；
- **修订既有**：`tests/test_repl_completer.py`——/model 参数补全（全列/前缀过滤/dynamic callable）、/view 路径补全不回退；
- **新增**：`tests/test_skill_command.py`——/skill 解析（无参提示、未知名报错、省略描述、附描述）、pending_task 组装模板、repl 消费后置 None、当次生效（不污染 system prompt）、skills 文案无加载状态；
- 基线：全量 `pytest tests/ -q` ≥115 passed（修订用例数守恒或增加）；
- 冒烟：管道链路 `/help`、`/skills`、`/skill`（无参与未知名分支）、`/expand`（空态与会话缓冲回放）、`/exit` 退出码 0；
- 交互验收：两段式 Enter / 默认选中第一条 / Escape 跳过补全列入用户真实终端验收清单（§1.2 自动化边界声明）。

## 六、实现位置汇总（概设 §10 工程结构内）

| 需求 | 文件 | 改动性质 |
|---|---|---|
| F1 | src/glaucous/cli.py | 补全器扩展（ARG_COMPLETIONS、model_names）、Enter 两段式 key bindings、默认选中第一条 |
| F2 | src/glaucous/extensions/skills.py、src/glaucous/commands.py | 索引文本去加载状态、说明行 |
| F3 | src/glaucous/commands.py、src/glaucous/extensions/skills.py、src/glaucous/cli.py | /skill 命令、skill_text、pending_task 消费 |
| F4 | src/glaucous/cli.py、src/glaucous/commands.py | 动态区滚动重构、正文缓冲与落账、md 卡片删除、会话级缓冲与 /expand、轮末时序 |
| 测试 | tests/test_turn_collapse.py、tests/test_repl_completer.py、tests/test_skill_command.py | 修订 + 新增 |
