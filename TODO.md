# Glaucous TODO

> 按开发计划表维护每日进度勾选（评委可见的开发过程素材）。

## M0 原型闭环（8/27–8/28）

### Day 1（8/27）

- [x] 0.1 WSL2 环境检查（Python 3.11.15 经 Miniconda 环境就绪）
- [x] 0.2 GitHub 新建仓库（github.com/Panjiebin13/Glaucous，已推送）
- [x] 0.3 项目骨架：pyproject.toml、src/glaucous/ 包结构、pytest 配置
- [x] 0.4 LLM 客户端：OpenAI 兼容请求 + 重试退避 + 流式读取
- [x] 0.5 工具基座：Tool 协议、registry、JSON Schema 定义
- [x] 0.6 三个只读工具：read_file / list_dir / grep
- [x] 0.7 主循环 v0：请求 → tool_calls → 执行 → 回喂 → 终止
- [x] 0.8 简版 CLI：input 循环 + print 输出（无主题）

> 0.3~0.8 代码已按 SDD 流程（Plan Review 4 轮 + Code Review 3 轮）完成编码。
> ✅ **Day 1 验收已通过**（2026-08-27）：真实 LLM 端到端测试（deepseek-v4-flash），官方验收用例「看看这个项目的结构」正确回答（9× list_dir + 3× read_file）；另验证 grep 搜索、错误路径回喂、多轮上下文记忆、优雅退出，均符合设计契约。

### Day 2（8/28）

- [x] 0.9 bash 工具（含超时、UTF-8、kill；先全部放行）
- [x] 0.10 write_file / edit_file 工具（含唯一匹配校验）
- [x] 0.11 Plan 语义 v0：Plan 下不注册写工具（声明层隐藏）
- [x] 0.12 submit_plan + 三选一切换确认（简版交互）
- [x] 0.13 edit 前打印 diff、用户 y/n 确认（审批的雏形）
- [x] 0.14 会话 JSONL 落盘（--resume 可续）
- [x] 0.15 端到端验证：真实小项目完整走一遍修 bug 流程（2026-08-28 用户环境实测通过）

> 0.9~0.14 代码已按 SDD 流程（Plan Review 2 轮 + Code Review 2 轮）完成编码；
> ✅ **M0 验收已通过**（2026-08-28）：任务 0.15 端到端验证完成（需求→探索→方案→授权→修改→汇报）。

### Day 3 / M1 权限成型（8/29）

- [x] 1.1 工作区沙箱：realpath 规范化 + 前缀校验 + 符号链接解析；未指定默认当前目录
- [x] 1.2 危险命令分类器：首词白名单 + 参数模式表；未识别保守升级（后记：M1 验收实测后 cd 加入 SAFE 白名单——无害探测命令，复合段仍独立定级，65 用例全绿）
- [x] 1.3 Build 审批三选项：同意 / 同意同类型 / 拒绝附理由；结构化回喂
- [x] 1.4 授权策略：per-action / auto-approve；auto-approve 仍拦区外+破坏性
- [x] 1.5 Plan 模式 bash 白名单（只放行 SAFE）
- [x] 1.6 审计日志 audit.log
- [x] 1.7 单测：沙箱逃逸、分类器正反例、审批流、auto-approve 守卫（2026-08-28 补齐，61 用例全绿：test_workspace_escape / test_classifier / test_approval_flow / test_autoprivilege_guard；模式工具暴露矩阵、循环审批拦截不计熔断等扩展项仍留 M4）
- [x] 1.8 stdin 输入净化：Windows cp936 终端中文输入经 surrogateescape 产生孤立代理字符（如 \udcef），发往 LLM API / 写会话 JSONL 时抛 `UnicodeEncodeError: surrogates not allowed`（WSL 中文输入实测复现）；修复：cli.py 新增 `sanitize_input()`，对全部 4 处 `input()` 结果净化——无代理原样放行；有则还原原始字节按 UTF-8 → GBK → replace 降级（保留 surrogateescape 以便 GBK 二次解码，故不对 stdin reconfigure replace）；已验证 UTF-8/GBK 还原与兜底可编码

> 1.1~1.6 代码已按 SDD 流程（Plan Review 2 轮 + Code Review 8 轮，含分类器复合命令/管道/引号/重定向安全修复）完成编码；
> 按用户要求本轮未做运行验证，M1 验收（场景 A/C）待环境就绪后执行；测试债务登记于 Plan §9 由 M4 偿还。

### Day 4 / M2 记忆与上下文（8/30）

- [x] 2.1 glaucous.md 双层注入：extensions/rules.py（全局 ~/.glaucous/glaucous.md + 项目 <workspace>/glaucous.md，超 4000 字符附提醒），build_system_prompt 注入段（FR-20）
- [x] 2.2 memory_save：extensions/memory.py MemoryStore（双作用域 JSON 存储、原子写、去重刷新 last_used、Top-N 注入），tools/memory_tool.py（FR-21/22）
- [x] 2.3 ask_user：tools/interactive.py + CLI 提问卡回调（EOF→「用户未响应」控制信号，非交互环境不挂死）；BASE_PROMPT 增求助节奏引导（FR-17/18/19）
- [x] 2.4 Token 记账：context/budget.py（ASCII/4+CJK/1.5 估算、70%/85% 两档、build_report）+ loop 守卫点接线 + budget 占用条事件渲染（FR-25/31）
- [x] 2.5 L0 输出截断：safety/output_limit.py（>300 行或 >50KB，头 200+尾 50，完整输出落盘 outputs/）+ tools/output.py read_output 分段回取（单次≤1000 行）
- [x] 2.6 L1 裁剪：history._meta 记账入史 + context/compactor.trim_history（_meta 派生一行摘要、最近 K=2 轮豁免、_trimmed 幂等、submit_plan 决策回喂保留锚行原文）
- [x] 2.7 L2 压缩：compactor.compact_history（早期历史压成≤500 字合成摘要消息 + 最新方案锚段；失败降级 L1 加深，连续失败 2 次且仍 critical 走终止③）；SubmitPlanTool 方案落盘 .glaucous/plans/ + 决策回喂附锚行 + history.view() 视图层锚替换（方案全文不常驻 API 视图，概设 §5.2）+ ReadPlanTool 回读
- [x] 2.8 基线回归：既有 65 用例全绿（python -m pytest tests/ -q）；Code-First 测试债务（Day4 新增模块单测）登记于 Plan §9，由 M4 偿还；端到端验证（场景 B/E）按用户决定留待自行在 WSL 环境验证

> 2.1~2.7 代码已按 SDD 流程（spec Review 2 轮阻塞归零 + Code Review 待进行）完成编码；
> 上下文压缩管线（L1/L2/锚替换/预算终止）为纯内存变换，会话 JSONL 保留全量原文（resume 后重新裁剪）。

### Day 5 / M3 体验与扩展（8/31）
> 注：本节 3.1/3.2/3.3/3.7 的 UI 实现已由 M3-UI 分支替换（见下节「M3 CLI 主题渲染」，UI 以该节为准），
> 依赖声明以 M3-UI 侧为准；功能条目（3.4~3.6、3.8 及评审建议项）保留不变。


- [x] 3.1 theme.py 色板 + rich Theme 接入 + 终端降级（FR-30）
- [x] 3.2 渲染规范：⏺/⎿ 工具行、四类卡片、状态栏、Banner、意象图标（FR-30）
- [x] 3.3 prompt_toolkit 输入层 + 斜杠命令全集（FR-31）
- [x] 3.4 models.toml 注册表 + /model 切换 + 连通性校验（FR-26/27）
- [x] 3.5 skill 扫描 + 索引注入 + load_skill 惰性加载 + 2 个内置示例（FR-28）
- [x] 3.6 /init 生成 glaucous.md 草稿（FR-23）
- [x] 3.7 终端降级（truecolor/256/16 色）
- [x] 3.8 基线回归：既有 65 用例全绿（64 passed, 1 skipped）+ 斜杠命令全链路冒烟通过；M3 验收（界面视觉规范/多模型接力）留待用户 WSL 环境自行验证
- [ ] 测试债务：M3 新增模块单测（test_model_registry / test_skill_lazyload / test_theme_render / test_repl_commands）由 M4 偿还；「注册时连通性校验」偏移为切换时校验（概设 §6.1，若后续落地 /model add 向导应补注册时 ping，spec 评审 S1）

#### 代码评审建议项（r1，见 docs/reviews/202608290030-m3-day5-code-review-r1.md）

- [ ] S2 状态栏 bottom_toolbar 为纯文本：无占用条三档变色与档位附注、Build 徽标未附策略（prompt_toolkit 侧样式需另行接线）
- [ ] S4 pyproject package-data 用 assets/skills/*/SKILL.md，改 ** 防嵌套目录时 wheel 漏装（当前单层等价）
- [ ] S5 /init README 识别仅枚举 3 个固定文件名，未覆盖 spec 的 README* 通配
- [ ] S6 init_draft.scan_workspace 相对路径在 Windows 下分隔符混杂，建议统一 POSIX 风格
- [ ] S7 Banner 缺「模式占位」（仅显示模型名，spec §4.2 字面要求模型名与模式占位）
- [x] S1/S3/S8 现状确认（r2 说明）：S1 load_config 抛 RegistryError 由 cli 双捕获，行为等价；S3 budget critical 维持三档状态行形态（升警示卡会与状态行重复，登记本项待 M4 视觉验收时再定）；S8 ReplContext loop/pipeline 可空与 stream_state 字段为构造顺序必需的等价实现（r2 已接受）

#### 代码评审建议项（r2，见 docs/reviews/202608290021-m3-day5-code-review-r2.md）

- [ ] r2-S1 theme.py VT 启用借 os.system("") 隐式依赖 cmd.exe，后续可改 ctypes.SetConsoleMode 显式置位并包裹异常（健壮性增强）
- [ ] r2-S2 VT 启用行为未同步入 spec §4.1 字面，后续修订补述；M4 偿还 test_theme_render 时增加 win32 TTY 分支的 mock 断言

#### 合并评审建议项（r3，见 docs/reviews/202608311000-plan-m3-day5-experience-extensions-code-review-r3-20260829-1700.md；B1/B2 已修复关闭，r4 聚焦复审通过）

- [ ] r3-S1 /help 与 PT 补全未收录 /view：HELP_LINES / SLASH_COMMANDS 增补（commands.py + cli.py）
- [ ] r3-S2 /exit 双路径死代码与告别文案分叉：清理 commands._cmd_exit 死分支或恢复 🌅 文案并同步 spec §4.3
- [ ] r3-S3 rich 依赖上限丢失：恢复 rich>=13.7,<14 或修订 spec §4.7 并登记
- [ ] r3-S5 Banner 无模型名/模式占位（r1 S7 合并后偏离扩大）：Banner 第三行接 ctx.current_model 与模式段，M4 视觉验收处理
- [ ] r3-S4 M3-UI 已登记修复项现状未变（B-01/S-01/S-02）：并入上方 3.2r 待办随 M4 偿还

### Day 5 / M3 CLI 主题渲染（8/31）

- [x] 3.1 theme.py 色板 + rich Theme 接入（cli.py 全量 console.print/console.input 化：色板单一出口、动态内容统一 escape、流式正文逐字保真、手写 ANSI 门控 `_ANSI`/`import os` 清除）（FR-30）
- [x] 3.2 渲染规范：三张交互卡改 rich Table（theme.py `make_card` 单一出口：ROUND 圆角框、框内标题栏+分隔线、边框海盐青/标题天青/键名天青；审批卡键值两列「需要确认/命令/风险」、方案/提问卡标题栏+正文行）；事件行语义色（诊断晚霞橙/模式切换天青/工具名亮青/成功海草绿/失败陶土红）、提示符/恢复行/错误出口同步主题化；状态栏、意象图标待补（FR-30）
- [x] 3.2m Markdown 渲染接入：theme.py 定义 `markdown.*` 主题样式（标题天青/正文与加粗海鸥白/行内代码与引用海盐青/链接亮青/弱化晴空灰/列表圆点天青）；方案卡正文与提问卡 question 改 `Markdown()` 渲染——rich Markdown 不解析 console markup，方括号天然防注入，替代逐行 escape；流式终答保持纯文本（整块渲染与逐 token 流式冲突）；审批 detail diff/工具输出刻意不用（`-`/`+` 行会被解析成列表）
- [ ] 3.3 斜杠命令 + PromptSession 补全（/plan /build /compact /clear /resume /model /memory /rules /skills /stop）（FR-31）——输入层已完成，见 3.3i；FR-31 常驻状态栏可用 PT `bottom_toolbar` 承载（/view 已先落地，见 3.3v）
- [x] 3.3i prompt_toolkit 输入层接入：theme.py `PT_STYLE`（与 rich THEME 同一组色板常量派生、类名即语义名，带点号类名 `class:glaucous.title` 实测可解析，色板单一出口延伸到输入层）+ cli.py 主输入 `PromptSession`（`prompt_async` 接 asyncio 循环、↑↓ 历史 + Ctrl+R 搜索、`FileHistory` 持久化 `.glaucous/input_history` 跨会话可用、打不开文件退回内存历史）；`prompt_symbol` 返回值改 prompt_toolkit HTML（提示符随输入行归 PT 渲染，rich markup 方括号会被 PT 字面打印），拆 `prompt_mode()` 供非交互分支拼纯文本，模型名晴空灰弱化并列模式后（`🌊 plan · deepseek-v4-flash > `，读 `config.profile.model`，3.4 /model 后动态跟随）；非 tty（管道/重定向）回退 `console.input`，TODO 1.8 cp936 净化路径不变；顺手修 B-04：三处 `console.print(file=sys.stderr)` 传 rich 不支持的形参，「配置错误/本轮执行失败/工作区不存在」兜底路径触发即 TypeError 自崩（配置缺失实测复现），去掉 file 形参走主题 Console。验证：65 用例全绿 + 管道/pty 双端到端（pty 实测 PT 路径模型名/天青加粗语义色命中、/exit 契约、退出码 0）
- [x] 3.3i2 输入区布局收敛：删独立模式行与 ❯ 前缀，模式段并入输入行前缀（tty 走 prompt_toolkit HTML `<glaucous.title>🌊 plan > </glaucous.title>` 天青加粗，管道回退拼纯文本 `🌊 plan > `，build·每次审批/auto 随 state 每轮动态重算）；模型/ctx 行顶格（去 2 空格缩进）；对话末尾 budget 占用条渲染删除，占用信息并入头部模型行（`deepseek-v4-flash  ○ 48k/128k tokens`——ctx_ring 圆环三档变色保留承载档位、百分比数字删除、token 用量接圆环后），render_prompt_header 签名 percent → `BudgetReport`（build_report 直传，单一数据源）。验证：68 用例全绿（65 基线 + tests/test_compression_event.py 压缩意象事件 3 用例）+ 管道/pty 双端到端（管道回退 `🌊 plan > ` 前缀 + 退出码 0；pty 实测前缀天青加粗 ANSI `0;38;5;73;1m`、无 `❯`、/exit 告别正常）
- [x] 3.3v /view 文件渲染（3.3 斜杠命令首个落地）——`/view <路径>` 按后缀注册表（`_VIEW_RENDERERS`，27 后缀 → 四类）分发渲染：md→方案卡式 Markdown 卡片 / 代码→pygments 语法高亮（rich Syntax，不进卡片容器）/ txt·log→卡片原文 / csv·tsv→表格分列；共用防线：ws.check() 沙箱校验 + NUL 字节二进制检测（防伪装后缀）+ UTF-8 解码 + `MD_RENDER_MAX_LINES=200` 行数守卫；agent 路径 read_file 打开 .md 自动渲染卡片（非 md 维持默认摘要）
- [ ] 3.4 models.toml 注册表 + /model 切换 + 连通性校验（FR-26/27）
- [ ] 3.5 skill 扫描 + 索引注入 + load_skill 惰性加载 + 2 个内置示例（FR-28）
- [ ] 3.6 /init 生成 glaucous.md 草稿（FR-23）
- [ ] 3.7 终端降级（truecolor/256/16 色）（FR-30）
- [ ] 3.2r cli.py AI review 修复项：审批「拒绝理由」EOF/Ctrl+C 保护（B-01）、删 risk_icons 空 dict（B-03）、render_event state 形参（S-01）、ask_user 越界序号重问（S-02）

> 3.1 已完成并经 AI review（评审文档：docs/designs/202608311000-design-m3-day5-cli-console-migration.md，65 用例基线全绿）；
> 3.2 三张交互卡已完成 rich Table 化（评审 S-03/S-04 关闭），B-02（action.target escape）随卡片化保留修复；
> emoji 意象已对齐主题设计并核对 docs/rich_emoji.txt（Banner ☁、提问卡 🕊 想请教你；`:sunrise:`/`:sunset:` 不在 rich 表，恢复行保留字面 🌅）；B-01/B-03/S-01/S-02 仍为待办。
> 3.2m Markdown 接入完成（theme.py `markdown.*` 样式 + 方案卡/提问卡正文 `Markdown()` 化，65 用例基线全绿）。
> 3.3i prompt_toolkit 输入层完成（theme.py `PT_STYLE` + cli.py `PromptSession`，详见评审文档跟进四：docs/designs/202608311000-design-m3-day5-cli-console-migration.md）；B-04（`console.print` file 形参 TypeError）随接入当场修复，B-01/B-03/S-01/S-02 仍为待办。
> 3.3i2 输入区布局收敛 + budget 占用并入头部完成（详见评审文档跟进五：docs/designs/202608311000-design-m3-day5-cli-console-migration.md）。
> 3.3v /view 文件渲染完成（md 卡片 / 代码高亮 / 文本 / CSV 表格，agent 路径 .md 自动卡片化；两个方案存档 .glaucous/plans/20260829-160008 与 161209，详见评审文档跟进六）；/view 为 3.3 斜杠命令首个落地，其余斜杠命令与 Completer 补全、FR-31 状态栏仍待办。

### v1.1 反馈修复批次（F1~F4）代码评审建议项（r1，见 docs/reviews/202608292334-code-review-v11-feedback-fixes-r1.md）

- [ ] S1 make_on_event 的 ws 参数在 tool_end md 卡片删除后已无用途，后续可收窄签名（cli.py，需同步 rebuild_loop 与测试调用点）
- [ ] S2 SkillRegistry.loaded_names() 在 F2 去加载态后产品侧零调用，可评估移除或标注保留理由（extensions/skills.py，M3 既有 API，不在本批范围内动）
- [ ] S3 /skills 条目排版「名称 [来源] 描述」与 spec §二字面「[来源] 名称」不一致（要素完备，沿用前批排版，视觉验收时统一）
- [ ] 范围裁剪偿还项（spec §〇）：FR-31 常驻状态栏；思考过程跨 /stop 落盘持久化（/resume 后思考缓冲空态）；/skill 的 Tab 参数补全

### V1.1-M1 模式基座 spec 评审建议项（r2，见 docs/reviews/202608301150-spec-review-v11-m1-mode-base-r2.md）

- [ ] S10 loop.py L145~146 注释含「三选一①②」与「自然终止回归在上方分支 emit」两处失实表述，随 M1 实现一并修正（统一出口保留但触发场景变化）
- [ ] 范围裁剪偿还项（spec §〇）：授权策略持久化配置面（如 GLAUCOUS_APPROVAL_POLICY），关联计划表 V1.1-M3 任务 3.1 落地后评估

### V1.1-M1 模式基座代码评审建议项（r1，见 docs/reviews/202608310000-code-review-v11-m1-mode-base-r1.md；B1 已修复关闭）

- [ ] S1 文案措辞与 spec 模板存在超集式改写（语义等价）：planning.py 反馈回喂尾句、cli.py 方案卡选项「提出修改意见」、commands.py /plan 摘要，后续统一口径
- [ ] S2 test_mode_default_build.py test_approve_in_build_touches_no_state 断言空洞（confirm 闭包未接 state，断言恒真），改按 PLAN 下批准收敛规则写法使其真实约束
- [ ] S3 「三选一」字样以退役声明形式残留在 modes.py/planning.py/cli.py 等 5 处注释/docstring（spec §7.3「随改随清」口径合规），后续文档轮次统一清理

### 产品化打磨遗留（用户 WSL 验收反馈 2026-08-30）

- [x] 箭头选择重绘残影（实测：两个「请选择：」、选项重复、提示行残留）——根因：重绘偏移漏计提示行，旧块被挤下去逐次叠加。已修复：select_with_arrows 整块重绘（问题+选项+提示行）+ \x1b[J 清屏到底 + CJK 按显示宽度截行防折行（回归测试 TestRedrawProtocol），待用户真实终端复核
- [ ] /view 路径补全重复前缀：已选 /docs 后在其下继续补全出现 /docs/docs/…——应基于当前输入的已有后缀做相对补全（_workspace_path_candidates 修复方向：若 arg 已含 / 结尾的已存在目录，候选应相对该目录展开）
- [ ] /skill 技能名参数补全：ARG_COMPLETIONS 增 skill 段（候选来自 SkillRegistry.infos() 的技能名，前缀过滤），/skill 后应能提示可用技能
