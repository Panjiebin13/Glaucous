# CLI 主题化与 console 迁移：AI Review + 设计基线（M3 Day5）

- 评审时间 / 评审 agent：2026-08-31 / AI code reviewer（本会话）
- 评审范围：`src/glaucous/cli.py`（453 行，M3 3.1 全量 `console.print`/`console.input` 迁移后）
- 关联文档：`docs/Glaucous天青夏日主题设计.md`（视觉规范）、`src/glaucous/theme.py`（色板单一出口）、`docs/Glaucous开发计划表.md` M3 3.1/3.2
- 验证手段：静态逐条对照 + 只读实测（rich 转义机制、`console.input` 语义、真彩色 ANSI 码、渲染冒烟全路径：三选一/审批/提问/全部 render_event 事件）+ `python -m pytest tests/ -q`（65 passed）

## 评审结论

**有条件通过（修复项登记 TODO，不阻塞迁移合入）**——console 迁移完整：色板单一出口达成（cli.py 零散 hex/ANSI 硬编码清零）、动态内容统一 escape、流式正文逐字保真、手写 ANSI 门控清除（`_ansi_enabled`/`_ANSI`/`import os`）。发现 3 项应修问题（B-01~B-03，集中在 `make_decision_callback`）与 4 项建议（S-01~S-04），均已在 TODO.md Day5 登记。

## 阻塞事项（应修，已登记 TODO）

- **[B-01] 审批「拒绝理由」输入无 EOF/Ctrl+C 保护** — 位置：`make_decision_callback` 内 `decide()`，`console.input("拒绝理由（可留空）")` 位于外层 try/except 之外 — 后果：理由输入阶段按 Ctrl+D（EOF），`EOFError` 落入 repl 顶层 `except Exception` 兜底，打印「本轮执行失败：…」——语义错误（本应是「用户中断审批」→ reject）；按 Ctrl+C 则穿透到 repl 顶层显示「已中断本轮」（可用但不优雅）— 修复：reason 输入包独立 `try/except (EOFError, KeyboardInterrupt)`，返回 `ApprovalDecision(choice="reject", reason=None)` 或附「用户中断」标注。

- **[B-02] 审批卡头部 `action.target` 未 escape** — 位置：`decide()` 首行 `console.print(f"\n  ⏺ 需要确认：{action.kind} {action.target}{risk_note}")` — 后果：target 是文件路径，含 `[` 的路径（如 `a[b].txt`）会被 rich markup 解析吞掉，命令展示失真（detail 已 escape，此处遗漏）— 修复：`{escape(str(action.target))}`；`action.kind`/`risk_note` 为内部常量可豁免。

- **[B-03] `risk_icons = {}` 空字典死代码** — 位置：`decide()` 函数体顶部 — 后果：无引用空壳，误导维护者以为存在风险图标映射 — 修复：删除（M3 3.2 需要风险图标时按主题语义重建）。

## 建议（建议修复 / 可选优化）

- **[S-01] `render_event` 的 `state` 形参未使用** — 位置：`render_event(event, payload, state)` 签名 — 与 M2 Day4 评审 S-07 同项，仍未修 — 建议：删除形参，或加注释「为 M3 状态栏预留」（保留成本极低）。

- **[S-02] ask_user 越界数字输入原样回喂模型** — 位置：`make_ask_callback` 的 `ask()` — options 为 2 项时输入 `9`：`raw.isdigit()` 为真但越界，落入自由文本分支回喂模型 — 后果：候选序号输错无提示、错误文本直接进上下文 — 建议：越界数字重问一次或提示「无效序号，请重试」，再落自由文本。

- **[S-03] 固定宽度卡片边框窄终端折叠** — 位置：三处卡片（提问/审批/方案）边框行 — 全量 console 后 rich 默认 fold，终端宽度 < 边框长度（~44–50 字符）时换行破坏边框 — 建议：并入 M3 3.2 渲染规范统一处理（边框行 `soft_wrap=True` 或自适应宽度），当前影响面小。

- **[S-04] 卡片主题化进度不均** — 方案卡已按主题（标题天青/正文海鸥白/选项语义色）；提问卡与审批卡仍纯文本 — 正是开发计划表 M3 3.2（FR-30）剩余工作：审批卡按主题设计 §2.4（命令海泡沫白加粗、风险落日橙、选项天青）、提问卡按 §2.5（正文海泡沫白、选项天青）、卡片边框海盐青 — 登记为 3.2 输入，不阻塞本次合入。

## 已验证机制（rich 实测留档，防止重复踩坑）

| 机制                      | 实测结论                                                                                                   | 影响                                                                           |
| ------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `console.input` 无 stream | 内部调 builtin `input()`：EOF 抛 EOFError、Ctrl+C 抛 KeyboardInterrupt、去尾换行                           | 与旧 `input()` 语义完全一致，`except (EOFError, KeyboardInterrupt)` 可原样保留 |
| `\[` 反斜杠转义           | 渲染回字面 `[`                                                                                             | 审批提示 `[a]/[b]/[c]` 必须用 `\[a]` 转义（字母开头必转）                      |
| `[[]` 旧式转义            | 在现代 rich 无效，原样显示 `[[]a]`                                                                         | 勿用                                                                           |
| `[b]` 标签解析            | 解析为 **bold 样式**（吞字 + 后续文本带粗）                                                                | 字母开头方括号在提示/动态内容中必须转义                                        |
| `escape()` 作用域         | 仅转义 `\[[a-z#/@][^[]*?]` 形态；`[1/2/3]`（数字开头）、`[████]`（非 ASCII）天然安全                       | 进度条、序号列表无需转义                                                       |
| 流式正文                  | `console.print(..., markup=False, emoji=False, soft_wrap=True)` 逐字保真                                   | 模型输出 `[...]`/`:emoji:` 不吞不转、不硬换行                                  |
| 动态内容 escape           | 卡片正文/工具参数/结果/会话摘要统一转义                                                                    | 防 markup 注入（含模型可控文本）                                               |
| budget 三档色             | low→`glaucous.ok` #7FB685 / warn→`glaucous.warn` #F4A261 / critical→`glaucous.error` #E07A5F（真彩色实测） | 对齐主题设计 §2.2                                                              |
| Console 门控              | 自带 isatty / NO_COLOR / Windows conhost VT；`_ansi_enabled`/`_ANSI`/`import os` 已删除                    | 管道/重定向无 ANSI 泄漏                                                        |

## 覆盖表（对开发计划表 M3）

| 计划任务                                        | 状态                                 | 说明                                                                                                  |
| ----------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| 3.1 theme.py 色板 + rich Theme 接入             | ✅ 完成                              | theme.py 语义样式名 + 全局 console；cli.py 无散落 hex/ANSI（B-03 空 dict 除外）                       |
| 3.2 渲染规范（四类卡片/状态栏/Banner/意象图标） | 部分（方案卡 ✅，提问卡/审批卡 ⏳）  | 接续项见 S-04；状态栏、意象图标未动                                                                   |
| 3.3 prompt_toolkit 输入层                       | ✅ 完成（斜杠命令 ⏳，/view 已落地） | theme.py PT_STYLE + cli.py PromptSession，见跟进四；/view 渲染见跟进六；FR-31 状态栏待 bottom_toolbar |
| 3.4~3.7                                         | 未开始                               | 模型注册 / skill / /init / 终端降级                                                                   |

## 红线核查

- 凭据/密钥：无新增处理面 ✓
- 安全：动态内容统一 escape、流式正文关闭 markup（B-02 为遗漏项，已登记修复）✓
- 基线：`pytest tests/ -q` 65 passed；cli 无既有测试，交互路径单测由 M4 4.1/4.2 补齐

## 跟进（2026-08-31 实施后）

- **S-04 已关闭**：3.2 四类卡片着色完成——提问卡（§2.5：标题天青/正文海鸥白/选项天青）、审批卡（§2.4：前缀天青/命令加粗海鸥白/风险晚霞橙/detail 海盐青/截断提示晴空灰）、方案卡此前已完成；事件行同步主题化（诊断晚霞橙、模式切换天青、工具名亮青、结果成功海草绿/失败陶土红、提示符与恢复行天青、弱化行晴空灰、错误出口陶土红）。
- **B-02 已修复**：`action.target` 随主题化补上 `escape()`（实测 `a[b].txt` 字面显示）。
- 剩余待办：B-01（拒绝理由 EOF/Ctrl+C 保护）、B-03（risk_icons 空 dict）、S-01（render_event state 形参）、S-02（ask_user 越界序号）——已登记 TODO.md 3.2r。

## 跟进二（2026-08-31 收工，卡片 Table 化）

- **三张交互卡全部改 rich Table**：theme.py 新增 `make_card()` 构造器 + 3 个语义样式（`glaucous.card.border` 海盐青 / `glaucous.card.title` 加粗天青 / `glaucous.card.key` 天青）——ROUND 圆角框（`box.ROUNDED`）、标题渲染为**框内首行标题栏**（header + 分隔线，贴合 §2.3~2.5 mockup）、`key_value=True` 生成键值两列（审批卡「需要确认/命令/风险」）；卡片视觉单一出口，cli.py 只加行列数据。
- **S-03 已关闭**：固定宽度手绘边框的窄终端折叠问题随 Table 自动布局消除。
- **B-02 保留修复**：审批卡键值行的命令仍走 `escape()`（实测 `a[b].txt` 字面显示）。
- **emoji 意象对齐**：Banner ⛅→☁（§2.1 mockup）、提问卡 🙋请问→🕊想请教你（§2.5/§4 海鸥意象），均核对 `docs/rich_emoji.txt` 存在性；`:sunrise:`/`:sunset:` 不在 rich 表，恢复行保留字面 🌅。
- 验证：`pytest tests/ -q` 65 passed；强制 tty 真彩色渲染三卡，色值全部命中主题（天青 58,166,185 / 海盐青 155,209,217 / 海鸥白 234,244,244 / 晚霞橙 244,162,97 / 海草绿 127,182,133）。

## 跟进三（2026-08-31，Markdown 渲染接入）

- **theme.py 新增 17 个 `markdown.*` 语义样式** + `from rich.markdown import Markdown` 单一出口：标题 `h1`~`h6` 加粗天青（h1 自动居中）、`paragraph`/`strong`/`em`/`item` 海鸥白、`code`/`code_block`/`block_quote` 海盐青（引用斜体）、`link` 亮青、`hr`/`link_url`/`s`/`kbd` 晴空灰、`item.bullet` 天青；代码围栏走 pygments 默认 monokai（可 `Markdown(code_theme=...)` 定制）。
- **方案卡正文与提问卡 question 改 `Markdown()` 单格渲染**：替代逐行 `escape()`——rich Markdown 不解析 console markup，`[结构]`/`[注]` 方括号天然防注入，模型 markdown（标题/有序列表/引用/代码块）按主题色板展示。
- **明确不 Markdown 的位置**：流式终答（`text` 事件逐 token 流式与整块渲染冲突，保持 `markup=False` 纯文本保真）、审批 detail diff（`-`/`+` 行会被解析为列表/引用）、工具输出与结构化状态行。
- 验证：方案卡/提问卡强制 tty 渲染命中主题色值；`pytest tests/ -q` 65 passed。

## 跟进四（2026-08-31，prompt_toolkit 输入层接入，M3 3.3 前半）

- **技术分工落地**（概设 §4「rich（Theme/渲染/diff）+ prompt_toolkit（输入）」）：渲染层零改动，prompt_toolkit 只接管 REPL 主输入；对照概设 §8.5「色板单一出口」与 FR-30 无框优先复核一致。
- **theme.py 新增 `PT_STYLE`**（`prompt_toolkit.styles.Style.from_dict`）：与 rich `THEME` 同一组色板常量派生，类名即 rich 语义名——带点号类名 `class:glaucous.title` 实测可正常解析，色板单一出口延伸到输入层（/theme 换色只改常量一处，rich/pt 两侧同时生效）；`card.*`/`markdown.*` 为 rich 专属，不在此重复。
- **cli.py 主输入换 `PromptSession`**：`await session.prompt_async(...)` 与 repl 的 asyncio 循环正确配合（↑↓ 历史 + Ctrl+R 搜索 + 语义样式）；`FileHistory` 持久化到 `.glaucous/input_history`（跨会话历史，`.gitignore` 已覆盖该目录；打开失败 OSError 退回内存历史）。
- **提示符迁移**：`prompt_symbol` 返回值从 rich markup 改为 prompt_toolkit `HTML`（`<glaucous.title>🌊 plan </glaucous.title>> `）——提示符必须由输入方渲染（行编辑/重绘所有权归 PT），rich markup 方括号会被 PT 字面打印；拆出 `prompt_mode()` 供非交互分支拼纯文本提示符。
- **非交互降级闸门**：stdin/stdout 任一非 tty（管道/重定向，TODO 1.8 场景）回退 `console.input`，cp936 净化路径与既有行为一致（PT 在管道下可读但会向输出打 "not a terminal" 警告，故不启用）。
- **[B-04]（新发现，当场修复）**：cli.py 三处 `console.print(..., file=sys.stderr)` 传了 rich `Console.print` 不支持的形参——「配置错误」「本轮执行失败」「工作区不存在」三条兜底路径一旦触发即 `TypeError` 自崩（配置缺失实测复现）；修复：去掉 `file` 形参走主题 Console（stdout）。已登记 TODO.md 3.3i。
- 验证：`pytest tests/ -q` 65 passed；管道端到端（回退路径：纯文本提示符 + `/exit` 告别，行为不变）；pty 伪终端端到端（PT 路径：提示符天青加粗 ANSI `38;5;73`、`/exit` 契约、退出码 0）。
- 已知边界：不应答光标位置查询（CPR）的哑终端会先打印一行 CPR 警告再降级继续工作；真实终端与 Windows conhost（Win32 API 路径）不受影响。
- **提示符并列模型名**（追加）：模型段以晴空灰弱化接在模式后（`🌊 plan · deepseek-v4-flash > `，概设 §8.4「徽标 | 模型」形态在提示符上的前驱），读 `config.profile.model`（默认 `DEFAULT_MODEL = deepseek-v4-flash`，`GLAUCOUS_MODEL` 可覆盖）；3.4 /model 切换落地后改为动态跟随。
- 剩余：3.3 后半（斜杠命令 + `Completer` 补全，直接挂在本 `PromptSession` 上）与 FR-31 常驻状态栏（PT `bottom_toolbar` 承载）。

## 跟进五（2026-08-31，输入区布局收敛 + budget 占用并入头部，3.3i2）

- **布局 3 行 → 2 行**：独立模式行（`🌊 plan >`）与 `❯` 输入前缀删除，模式段并入输入行前缀——tty 走 prompt_toolkit HTML `<glaucous.title>🌊 plan > </glaucous.title>`（PT_STYLE 天青加粗，色板单一出口不变），管道回退拼纯文本 `🌊 plan > `；build·每次审批 / build·auto 随 state 每轮动态重算，mode_changed 事件反馈（`◆ 已进入 Build 模式`）保留不变。
- **模型/ctx 行顶格**：去 2 空格缩进，与输入行左对齐。
- **budget 占用条删除并并入头部**：对话末尾 ctx 占用条（12 格进度条 + 百分比 + warn/critical 提示文案）不再渲染（事件仍由 loop 发送，仅 UI 不打印）；占用信息合并进输入区头部模型行——`deepseek-v4-flash  ○ 48k/128k tokens`：ctx_ring 圆环三档变色保留（形状承载占用档位），百分比数字删除，token 用量（`used//1000k/limit//1000k`，换算与旧占用条同源）接圆环后。
- **render_prompt_header 签名**：`(model_name, percent: float)` → `(model_name, report: BudgetReport)`——build_report 直传，percent/used/limit 单一数据源。
- 验证：`pytest tests/ -q` 65 passed；管道端到端（回退路径：头部 `○ 0k/128k tokens`、`🌊 plan > ` 前缀、/exit 退出码 0）；pty 伪终端端到端（tty 路径：`🌊 plan > ` 前缀天青加粗 ANSI `0;38;5;73;1m`、无 `❯`、告别输出正常）。

## 跟进六（2026-08-29，/view 文件渲染——3.3 斜杠命令首个落地）

- **需求来源**：用户主动新增（计划表 3.3 未列）；方案两份存档 `.glaucous/plans/20260829-160008`（md 卡片渲染）、`.glaucous/plans/20260829-161209`（非 md 渲染：代码高亮/纯文本/CSV）。
- **theme.py 新增 4 个文档渲染函数**（卡片视觉单一出口原则延续）：`render_markdown_doc`（make_card + `Markdown()`，markdown.\* 色板）、`render_code_doc`（rich `Syntax` 语法高亮 + 行号，**不进卡片容器**——代码需全宽不折行，标题行单独打印；lexer 经 `_lexer_for` 按扩展名选择，未知回退纯文本）、`render_text_doc`（卡片原文）、`render_csv_doc`（csv.reader 解析 → make_card 分列，首行作表头；解析失败回退原文渲染）。
- **cli.py `_cmd_view` 重构为分发器**：`_VIEW_RENDERERS` 后缀注册表（27 后缀 → markdown/code/text/csv 四类，新增类型只加一行）；共用防线按序执行——`ws.check()` 沙箱校验（WorkspaceEscape 提示越界）→ 存在性/文件类型 → `_detect_binary` NUL 字节二进制检测（比后缀判断可靠，防伪装后缀）→ UTF-8 解码 → `MD_RENDER_MAX_LINES=200` 行数守卫（超长不整屏刷出）；未知后缀/二进制提示走 read_file。
- **agent 路径**：`_render_md_tool_end` 让 read_file 打开 .md 时自动渲染方案卡式卡片（tool_end + path 以 .md/.markdown 结尾 + 结果 ok + 行数 ≤ 上限 → `ws.check()` 后读**文件原文**渲染——read_file 结果带行号，直接用会破坏 md 结构）；超长维持默认 3 行摘要并提示可 /view；非 md 文件维持默认摘要（方案中的通用化 `_render_doc_tool_end` 为可选步骤，本轮未做）。
- **/view 是 3.3 斜杠命令的第一个落地**：REPL 输入循环与 /exit /quit 并列分发，Plan/Build 均可用（只读）；其余斜杠命令（/plan /build /compact /clear /resume /model /memory /rules /skills /stop）与 Completer 补全仍为 3.3 待办。
- 验证：`pytest tests/ -q` **68 passed**（65 基线 + tests/test_compression_event.py 压缩意象事件 3 用例，该测试随 3.3i2 追加）；计划内验证项：/view 代码高亮、README.md 卡片回归、非 UTF-8/二进制拦截不崩溃。
- 已知边界/待办：pygments 内置主题（monokai）与天青色板未对齐（记录待办）；Syntax 默认不折行，长行横向滚动（可加 `word_wrap=True` 权衡）；CSV 含引号/多行字段时 `csv.reader` 正确性有限（异常回退原文）；B-01/B-03/S-01/S-02 与 3.4~3.7 状态不变。
