# v1.1 前置产品化打磨：技能创建 / 补全增强 / 思考折叠 / 用量展示 / 交互升级

> 版本：v1.1（评审修复版）
> 日期：2026-08-29
> 上游依据：docs/编程智能体需求文档.md（FR-08/10/11/26/27/28/30/31/33）、docs/编程智能体概要设计说明书.md §5/§6/§7/§8/§9/§10、docs/Glaucous开发计划表.md、docs/Glaucous天青夏日主题设计.md
> 前置状态：M3 已合并（feature/m3-day5 ← M3-UI，评审 r4 通过），本批为 v1.1 开发前的产品化细节打磨
> 决策记录（用户已确认）：skill 仅创建到项目工作区；思考折叠用 /expand 展开；md 渲染为流式结束后追加卡片；模型模板首次启动生成；缓存字段有则显示无则省略；附加项 A/B/C/D 全部纳入
> 评审修复记录：首轮（B1~B4/S1~S10）、r2 复审（r2-B1/r2-B2/r2-S1~S4）、r3 复审（r3-B1/S1/S2，报告见 docs/reviews/ 同名 spec-review 系列）已全部吸收

## 〇、目标与范围

让产品在进入 v1.1 前具备更完整的「产品化」体验：模型可引导用户创建技能、输入补全覆盖命令与文件路径、思考过程可折叠回看、每轮会话展示用量与缓存命中、选项改为箭头键选择、最终回答渲染为 Markdown。

**需求编号**：
- R1 create_skill 内置技能（模型引导用户创建技能）
- R2 斜杠命令前缀补全 + 路径参数补全
- R3 思考过程折叠 + /expand 展开
- R4 模型注册表内置模板（含 deepseek-v4-pro）
- R5 每轮会话用量与缓存命中率展示
- R6 箭头键选项选择（对齐 Claude Code 交互）
- R7 最终回答 Markdown 渲染
- A 附加：Banner 显示当前模型名与模式（r3-S5 偿还）
- B 附加：审批拒绝理由的 EOF/中断保护（TODO 3.2r B-01 偿还）
- C 附加：/exit 死代码清理（r3-S2 偿还）
- D 附加：恢复 rich 依赖上限（r3-S3 偿还）

**范围裁剪（本轮不做）**：
- M4 既有测试债务（test_model_registry / test_skill_lazyload / test_theme_render / test_repl_commands）仍留 M4；
- /theme 暗亮切换；
- FR-31 常驻状态栏（bottom_toolbar）：留待 M4（TODO 3.3 条目已登记该去向）；
- skill 全局目录（~/.glaucous/skills/）写通道（用户已裁定仅项目级）；
- 思考过程折叠在管道模式的行为（管道全量输出，现状保持）；
- 箭头选择中的自由文本输入（拒绝理由、无选项提问仍走文本输入）。

**测试声明**：本轮包含测试（§十），随代码一并交付；既有 67 passed 1 skipped 基线不得回退。

## 一、R1 create_skill 内置技能

### 1.1 交付物

新增 `src/glaucous/assets/skills/create-skill/SKILL.md`（打包已含 `assets/skills/*/SKILL.md` package-data，单层目录无需改打包配置）。内置技能数由 2 增至 3（三层扫描自动收录，无需注册代码改动）。

### 1.2 SKILL.md 内容约束

- frontmatter：`name: create-skill`；`description` 必须写明触发场景（模型据此决定是否 load_skill），形如：「当用户想要创建、定制或沉淀一个新技能（skill）时使用：引导确认技能用途与触发场景，并在项目工作区生成规范的 SKILL.md」；
- 正文为给模型的执行指令，必须包含且仅包含以下要点：
  1. 先与用户确认技能用途与触发场景（信息不足时用 ask_user）；
  2. 技能目录命名规范：小写、连字符分隔（如 `release-notes`）；
  3. 目标路径固定为 `<工作区>/.glaucous/skills/<name>/SKILL.md`（项目级，符合工作区约束，用现有写文件工具即可，不得写工作区之外）；
  4. 文件模板：`---` frontmatter 必含 `name`（与目录名一致）与 `description`（写成明确触发场景句），正文为指令；
  5. **模式提示**：写文件工具仅在 Build 模式可用——若当前处于 Plan 模式，须先声明进入 Build（审批策略沿用当前模式约定）；
  6. 创建后复读文件自校验格式；
  7. 明确告知用户：新技能在下次 `/clear` 或重启会话后生效（扫描发生在会话启动时）。

### 1.3 不变量

- 该技能不引入任何新工具/新代码路径，仅新增资产文件；/skills 展示来源为「内置」。

## 二、R2 斜杠命令前缀补全 + 路径参数补全

### 2.1 命令面

- `cli.SLASH_COMMANDS` 增补 `/view`、`/expand`（R3 引入）；
- **单一数据源（S10 修复）**：`commands.py` 新增 `COMMAND_META: dict[str, str]`（命令名 → 一句摘要），`HELP_LINES` 由它拼装；cli 的补全器 meta 也从 `commands.COMMAND_META` 导入（复用既有 cli → commands 导入方向，不产生循环导入）。`/view <文件路径>`、`/expand` 的说明一并入表；
- 三处一致约束保持：SLASH_COMMANDS / HELP_LINES（经 COMMAND_META）/ handle_command 分派表。

### 2.2 补全器（替换现有 WordCompleter）

新增 `cli.make_repl_completer(workspace: Path) -> Completer`：

- **命令段**：输入以 `/` 开头且不含空格 → 补全命令名（`complete_while_typing=True`，即键入 `/` 立即列出全部命令）；每个候选带 meta 描述（取自 `commands.COMMAND_META`）；
- **路径段**：命令名属于 `PATH_ARG_COMMANDS = {"/view"}` 且已输入空格后 → 工作区内路径补全：
  - 基于相对/绝对前缀遍历 `workspace` 子树，目录与文件都列出（目录名尾缀 `/` 便于继续深入）；
  - 排除目录：`.git`、`__pycache__`、`.pytest_cache`、`node_modules`、`.glaucous/sessions`；
  - 单层候选上限 200 条（超大目录防卡），超出追加一个 `…（更多，继续输入以缩小范围）` 只读提示项；
  - 遍历异常（权限等）静默返回空候选，不抛错；
- **其他段**（自由对话）：不弹补全。
- 管道/纯文本模式无补全器（降级路径不受影响）。

### 2.3 验收点

- 键入 `/` 立即出现含 /view、/expand 的命令列表；键入 `/vi` 过滤到 /view；
- `/view ` 后出现工作区文件/目录候选；键入子串可过滤。

## 三、R3 思考过程折叠 + /expand

### 3.1 时序模型（B3/r2-B1/r2-S1 修复 + r3-B1/S2 修复：思考区只收纳非 text 事件，text 增量维持现状逐字实时显示，无全文重述）

既有事实：`stream_state` 仅 `{"printed": bool}`；loop 对**每次** LLM 响应（含带 tool_calls 的中间步正文）都发射 `text` 事件，流式期间无法预知当前 text 是否最终回答。因此文本流**不参与分流判定**：

- **`agent/loop.py` 新增事件**：`run` 循环中命中「无 tool_calls → 返回」分支（最终回答确定）时，在 `return` 前发射 `self._on_event("stream_end", {})`。这是本轮**唯一**的 loop 改动（仅作为折叠区内容定格的信号，不承担渲染职责）；
- **TTY 且折叠开启**（默认开启；`GLAUCOUS_COLLAPSE=off` 可关）：
  - **`text` 增量（含中间步与最终回答）：维持现状逐字实时打印，不进思考区**（与用户「流式结束后追加卡片」决策一致，无重复呈现）；
  - **非 text 事件**（tool_start/tool_end/budget/compressed/模式切换等）不逐条直接打印，而是渲染进一个 `rich.live.Live` 动态区（高度上限 `THINKING_MAX_LINES = 8`，滚动显示最近事件 + 顶行计数「⚙ 思考中 · N 步」；N = 已收纳的非 text 事件条数）；
- **轮末渲染**（r2-S1 修复：发生在 `await ctx.loop.run(task)` 返回后的 `finally`，而非 stream_end 事件内）：
  1. `Live.update(摘要行)` 后 `Live.stop()` —— 思考区原地收缩为一行：`💭 思考过程（N 步 · ↑Xk ↓Yk tokens）— /expand 查看`（token 段取本轮 `turn_usage` 合计，无 usage 数据时省略 token 段）；
  2. 按序执行 R7（md 卡片，追加在流式回答之后）、R5（用量行）渲染；
  3. 清空 `ctx.turn_events` 与 `ctx.turn_usage`（调用 `commands.reset_turn_buffers(ctx)`）；
- **/expand**：打印当前缓冲的全部条目（复用 `render_event` 逐条渲染，加「── 思考过程（本轮）──」分隔头）；缓冲为空提示「本轮暂无可展开的思考过程」；仅读缓冲，不影响状态。

### 3.2 阻塞交互与 Live 区的共存时序（B4 修复）

既有事实：ask/decision/拒绝理由/重试提示**不是** on_event 事件，而是 CLI 注入的同步回调直接打印，且为阻塞调用（`console.input` / 箭头选择）。与 Live 区共存的约定：

- `ReplContext` 新增 `live_hooks: dict`（含 `pause: Callable[[], None]` / `resume: Callable[[], None]`），由 repl 启动时按折叠模式注入真实实现（折叠关闭/管道时为 no-op 空函数）；
- 四个阻塞点进入前调 `ctx.live_hooks["pause"]()`、返回后调 `resume()`：`make_ask_callback.ask`、`make_decision_callback.decide`（含拒绝理由输入）、`prompt_plan_decision`、`ThemeRenderer.retry`；
- pause = `Live.stop()`（保留已渲染内容，交互输出正常打印），resume = `Live.start()` 恢复动态区；异常路径用 try/finally 保证 resume；
- **事件缓冲范围**：`ReplContext` 新增 `turn_events: list = field(default_factory=list)` —— 记录两类条目：① 非 text 的 on_event 事件 `(event, payload)`（text 增量不缓冲，与思考区收纳范围一致）；② 交互伪事件 `("ask", {...})` / `("decision", {...})` / `("plan_decision", {...})`（回调内部记录，供 /expand 完整回看本轮人机交互）。

### 3.3 降级与异常安全（r2-B2 修复：异常路径清理责任归 repl 层，不依赖事件发射）

- 非 TTY（管道/重定向）或 `GLAUCOUS_COLLAPSE=off` → 不开 Live，事件维持现状逐条实时打印（含 text 增量）；turn_events 仍记录（管道下 /expand 可用）；
- Live 启动失败（终端不支持）→ 降级为实时打印，本轮不再尝试；
- **异常 raise 路径**（`LLMError`、Ctrl+C 中断从 loop 抛出、预算熔断外的其他异常）不经任何提前返回分支，`stream_end` 不会发射：轮末渲染/收缩/清理的**执行责任归 cli repl 层** —— repl 的 `try: answer = await ctx.loop.run(task)` 用 `finally` 统一执行 §3.1 步骤 1~3（Live 收缩；异常时跳过步骤 2 的卡片渲染，用量行仍按已收集数据打印且遵循 §5.3 无数据抑制规则；缓冲清理总是执行），`stream_end` 事件不承诺在异常路径发射；
- /clear、/resume：调用 `commands.reset_turn_buffers(ctx)` **显式**清空（S3 修复：ctx 为同一对象复用，字段不会随 rebuild_loop 自然重置）。

### 3.4 不变量（r3-B1 修复：口径统一后自然成立）

- 折叠只改变**呈现**，不改变事件语义：审计、预算、历史消息均不受影响；text 增量在折叠开/关两种模式下输出内容完全一致（逐字保真）；
- 摘要行的 N 与 /expand 打印条数一致（两者同为「非 text 事件 + 交互伪事件」口径）。

## 四、R4 模型注册表内置模板

### 4.1 交付物

新增 `src/glaucous/assets/models.toml.example`；`pyproject.toml` package-data 追加 `assets/*.toml.example`。

模板内容（两个档案，密钥仅指向环境变量名，注释说明）：

```toml
# Glaucous 模型注册表（密钥零存储：只写环境变量名，绝不写密钥本身）
# 使用前请先 export 对应环境变量，例如：export GLAUCOUS_API_KEY=sk-****

[models.deepseek-v4-flash]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
api_key_env = "GLAUCOUS_API_KEY"

[models.deepseek-v4-pro]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
api_key_env = "GLAUCOUS_API_KEY"
```

### 4.2 首次启动生成逻辑

`llm/registry.py` 新增 `ensure_models_toml() -> None`，在 `load_registry` 文件缺失分支**之前**调用：

- `~/.glaucous/models.toml` 不存在 → 尝试从包资产读取模板写入（目录不存在则创建）；写入成功后由原有加载流程正常解析（默认档案 = 首段 `deepseek-v4-flash`，与既有 `GLAUCOUS_DEFAULT_MODEL` 语义兼容）；
- 模板读取/写入任何失败（打包形态异常、权限）→ **静默回退既有 env 单档案兜底**，不阻断启动；
- 文件已存在 → 绝不覆盖、绝不修改。

### 4.3 验收点

- 全新环境启动：`~/.glaucous/models.toml` 自动生成；`/model` 列出 deepseek-v4-flash（当前）与 deepseek-v4-pro；
- 已有 models.toml 的环境：文件内容逐字节不变。

## 五、R5 每轮用量与缓存命中率

### 5.1 采集（llm/client.py；S1 修复：发射主体明确为 LLMClient，不经 loop 转发）

- `LLMClient.__init__` 签名追加 `on_usage: Callable[[dict], None] | None = None`（与既有 `on_retry` 同构造注入；`switch_profile` 不影响该回调）；
- `_chat_once` 流式请求 kwargs 增加 `stream_options={"include_usage": True}`；
- **网关兼容降级（S6）**：若首次请求因 `stream_options` 不被支持而失败（不可重试类异常）→ 去掉该参数原样重试一次；再失败走正常错误流（不影响 429 重试链语义）；
- 迭代 chunk 时若 `getattr(chunk, "usage", None)` 非空且 `on_usage` 已注入 → 回调发射归一化 payload：
  ```
  {
    "prompt": usage.prompt_tokens,
    "completion": usage.completion_tokens,
    "cache_hit": <int|None>,   # DeepSeek: usage.prompt_cache_hit_tokens
    "cache_miss": <int|None>,  # DeepSeek: usage.prompt_cache_miss_tokens
  }
  ```
  取值一律 `getattr(..., None)`，缺失即 None；兼容 OpenAI 风格：若存在 `usage.prompt_tokens_details.cached_tokens` 且 DeepSeek 字段缺失，则 `cache_hit = cached_tokens`、`cache_miss = prompt - cached_tokens`；
- chunk.usage 为 None 的既有 mock/真实流不受影响（不回调）。

### 5.2 累计与渲染（cli.py / commands.py；S2 修复：统一「本轮累计」口径）

- `ReplContext` 新增 `turn_usage: dict = field(default_factory=...)`，结构 `{"prompt": 0, "completion": 0, "cache_hit": None, "cache_miss": None}`；cli 构造 `LLMClient` 时注入 `on_usage` 累加器：数字字段求和；`cache_hit`/`cache_miss` 首次收到非 None 值时由 None 转为 0 基线后再累加（全程 None 表示供应商无缓存数据）；
- **口径界定（r2-S2 修复）**：turn_usage 只累计**任务轮内**（`run()` 调用期间）的 usage；`/compact` 命令的压缩调用发生在轮间，其 LLM 用量经 on_usage 计入前立即忽略（实现：cli 在 /compact 调用期间临时置 `ctx.counting_usage = False` 门控，默认 True）；loop 内 L2 压缩发生在轮内，计入本轮口径（符合「本轮实际消耗」语义）；
- **轮末渲染顺序**（与 §3.1 一致）：折叠摘要行 → R7 md 卡片（追加在流式回答之后）→ **用量行**：
  `⏱ ↑12.3k ↓456 tokens · 缓存命中 82%`
  - 数值格式：`<1000` 原样，`≥1000` 保留一位小数加 `k`；
  - 命中率 = `cache_hit / (cache_hit + cache_miss)`，四舍五入整数百分比；`cache_hit is None` 时该行只打到 `tokens` 为止（不显示缓存段）；
  - 该行经主题样式（glaucous.muted），数值为纯数字字符串无需 escape；
- 管道模式同样打印（纯文本无样式）；
- 折叠摘要行（§3.1）的 token 段与该行同源（turn_usage 累计），两处数字一致。

### 5.3 不变量

- 供应商不返回 usage → 整轮无 usage 行、摘要行无 token 段，其余流程逐字节不变。

## 六、R6 箭头键选项选择

### 6.1 实现选型（B2 修复：不用 prompt_toolkit Application）

三处回调均为**同步**函数且在运行中的 asyncio 事件循环内被调用（`await registry.dispatch` 链路），prompt_toolkit `Application` 无法在已运行循环中同步 `run`。因此选择器采用**终端原始按键读取**实现，与事件循环无关：

`cli.select_with_arrows(question: str, options: list[str]) -> int | None`：

- 按键读取：Windows 用 `msvcrt.getwch()`，POSIX 用 `termios/tty` 临时切 raw 模式（try/finally 还原终端属性）；
- 键语义：↑（含 `k`）/↓（含 `j`）移动（循环）、Enter/回车 确认返回索引、**Esc 与 Ctrl+C 取消返回 None**；ESC 序列判别：读到 `\x1b` 后若后续非 `[A`/`[B` 则视为取消键；
- 渲染：每步用 ANSI 光标上移重绘选项块（当前项 `❯` 高亮 + `glaucous.title` 样式，其余 `glaucous.text`），顶部 question（`glaucous.sub`），底部提示「↑↓ 选择 · Enter 确认 · Esc 取消」；全部经 theme.console 输出；
- 任何构造/运行异常（含 `KeyboardInterrupt`）→ 返回 None 由调用方走数字回退；
- 可测性：按键源抽象为注入参数 `read_key: Callable[[], str] | None = None`（默认按平台取），单测注入伪按键序列。

### 6.2 选项集与接线（B1 修复：对齐既有三选项契约）

- **审批卡**（`make_decision_callback`）：选项为既有三选项（概设 §5.3、FR-11）——「同意」「同意同类型」「拒绝」，映射 `ApprovalDecision.choice` 的 `approve` / `approve_type` / `reject`；选中「拒绝」后进入既有拒绝理由文本输入（含 B 项保护）；取消（Esc）= 拒绝、理由「用户取消」；**DANGEROUS 呈现取舍（r2-S3 决策）**：卡片仍统一展示三选项（不另做 DANGEROUS 分列），安全语义由既有 `gate` 守卫兜底（DANGEROUS 不受同类型豁免，与现状一致），避免本轮扩改审批卡形态；
- **方案卡**（`prompt_plan_decision`）：选项为既有一/二/三（FR-08、planning.CHOICE_*）——「执行（逐次审批）」「执行（自动批准）」「继续讨论一下」（第三项文案对齐 FR-08 字面，r2-S4 修复）；取消（Esc）= 选择三（继续讨论），`feedback` 置「用户取消」（r2-S4 修复：`PlanDecision` 无 reason 字段，用户取消意图统一落 `feedback`）；
- **提问卡**（`make_ask_callback`）：options 非空（≤6，模型给定）→ 箭头选择，选中返回选项原文，取消返回 None（与现有「未响应」语义一致）；无 options → 维持现有文本输入；
- 触发条件：TTY + 非纯文本降级 + options/选项数 ≥2；否则走现有数字输入卡；
- 数字输入回退路径的所有既有行为（越界回喂、空回答处理）保持不变。

### 6.3 验收点

- pty 环境：提问卡/审批卡（三选项）/方案卡（三选一）均可 ↑↓ 选择、Enter 确认、Esc 取消；
- 管道环境：三卡全部走数字输入，现有测试不回退。

## 七、R7 最终回答 Markdown 渲染

- 流式期间保持逐字保真（现状不变）；轮末（`run()` 返回后 `finally` 内，r3-S1 修复：不再以事件为锚点）追加渲染完整回答为 Markdown 卡片：
  - **回答文本来源（S4 修复，无二选一残留）**：`await ctx.loop.run(task)` 的返回值（loop 已聚合最终回答全文）；
  - `theme.py` 新增 `render_answer_card(text: str) -> None`：`make_card("🕊 回答")` + `Markdown(text)`；空文本/纯空白不渲染；
  - 触发条件：TTY 且回答非空；管道模式不渲染（纯文本已输出）；
- 顺序固定：流式原文 → md 卡片 → usage 行（R5）；
- **偏离声明（S5）**：概设 §8.4 约定「正文不套面板」，本卡片为用户明确决策的产品化呈现（对齐方案卡既有 Markdown 卡片形态），在此登记偏离，概设修订随 v1.1 文档轮次同步。

## 八、附加项

- **A（Banner，S8 修复）**：`render_banner` 第三行追加 `当前模型 {model} · 模式 {mode}`；签名改为 `render_banner(model_name: str, mode: str)`，**数据源统一为 `ctx.current_model`**（与输入区头部行同口径；/model 切换后语义一致，Banner 为启动快照不随切换刷新）；动态值经 `escape()`；
- **B（审批拒绝保护）**：`make_decision_callback` 拒绝理由的 `console.input` 包 `try (EOFError, KeyboardInterrupt)` → 视为理由「用户取消」继续拒绝流程（不再落入「本轮执行失败」兜底）；
- **C（/exit 死代码）**：删除 `commands.handle_command` 分派表中 `/exit`、`/quit` 分支与 `_cmd_exit` 函数（cli repl 内联拦截为唯一路径）；HELP_LINES 中 /exit /quit 条目保留；
- **D（依赖上限）**：`pyproject.toml` 与 `requirements.txt` 的 rich 约束改为 `rich>=13.7,<14`。

## 九、实现位置汇总（概设 §10 工程结构内）

| 需求 | 文件 | 改动性质 |
|---|---|---|
| R1 | src/glaucous/assets/skills/create-skill/SKILL.md | 新增资产 |
| R2 | src/glaucous/cli.py、src/glaucous/commands.py | 补全器新增、COMMAND_META 落 commands.py |
| R3 | src/glaucous/agent/loop.py（stream_end 事件）、src/glaucous/cli.py、src/glaucous/commands.py（turn_events/live_hooks 字段、reset_turn_buffers、/expand 分派） | 折叠渲染、缓冲、新命令 |
| R4 | src/glaucous/assets/models.toml.example、src/glaucous/llm/registry.py、pyproject.toml | 模板 + 生成逻辑 |
| R5 | src/glaucous/llm/client.py（on_usage 注入）、src/glaucous/cli.py、src/glaucous/commands.py（turn_usage 字段） | usage 采集与渲染 |
| R6 | src/glaucous/cli.py | 按键选择器 + 三处回调接线 |
| R7 | src/glaucous/theme.py、src/glaucous/cli.py | render_answer_card + 轮末（run 返回后）接线 |
| A | src/glaucous/cli.py | render_banner 签名 |
| B | src/glaucous/cli.py | 拒绝理由保护 |
| C | src/glaucous/commands.py | 删除死分支 |
| D | pyproject.toml、requirements.txt | 版本约束 |

## 十、测试声明（本轮包含）

新增/扩展测试（tests/）：

1. `test_usage_event.py`：mock 流式 chunk（含 usage chunk）→ usage payload 归一化（DeepSeek 字段 / OpenAI details 字段 / 无 usage 不回调）；`stream_options` 降级重试（首次参数报错 → 去参重试成功）；
2. `test_assets.py`（S9 归属）：`models.toml.example` 可被 tomllib 解析且两档案字段齐全、无 api_key 明文；`create-skill/SKILL.md` frontmatter 可被 `_parse_frontmatter` 解析、name 与目录名一致；模板缺失时生成到 tmp HOME、已存在不覆盖、模板损坏回退 env 兜底、生成后 `load_registry` 解析出两档案；
3. `test_turn_collapse.py`：turn_events 缓冲记录（仅非 text 事件 + 交互伪事件；text 增量不缓冲）、`reset_turn_buffers` 清理时机、/expand 空缓冲提示、摘要行 N 与 /expand 条数一致性、`GLAUCOUS_COLLAPSE=off` 行为、异常路径下 repl finally 清理仍执行（mock run 抛错断言缓冲已清）；
4. `test_repl_completer.py`：命令段补全（/ 触发、/vi 过滤、meta 取自 COMMAND_META）、路径段补全（候选含目录后缀 /、排除目录、异常静默）、其他段无候选；
5. `test_arrow_select.py`：`select_with_arrows` 注入伪按键序列（↑↓ 移动、Enter 返回索引、Esc 返回 None、循环移动边界）；
6. `test_answer_card.py`（S9 归属）：`render_answer_card` 卡片内容含标题与正文、空文本不渲染；管道/非 TTY 下 cli 不触发卡片（以渲染函数单测 + 触发条件断言覆盖）；
7. 箭头选择在真实审批链路的 TTY 交互不做自动化（需真实终端），以降级路径测试覆盖：管道下三卡均走数字输入（沿用既有审批测试模式，确认不回退）。

验收基线：`python -m pytest tests/ -q` 全绿（既有 67 passed 1 skipped + 新增），`python -c "import glaucous.cli"` 通过，管道端到端冒烟（/help、/model、/expand、/view、/exit）退出码 0。

## 十一、验收清单（用户环境）

1. 启动后 `/model` 列出两个档案；/help 含 /view 与 /expand；
2. 键入 `/` 立即弹命令补全；`/view ` 弹文件补全；
3. 发起一个会用工具的任务：思考区动态滚动 → 回答后收缩为摘要行 → `/expand` 回看（含交互记录）；
4. 每轮回答后出现 `⏱ ↑… ↓… tokens · 缓存命中 …`（供应商无缓存字段则无缓存段）；
5. 提问/审批（三选项）/方案（三选一）卡 ↑↓ 选择；管道模式数字输入不回退；
6. 最终回答下方出现 Markdown 渲染卡片；
7. 说「帮我创建一个技能」→ 模型加载 create-skill 并在 `.glaucous/skills/` 生成规范文件；/clear 后 /skills 可见；
8. Banner 含模型名与模式；审批拒绝时 Ctrl+C 不再报错兜底。
